#
# sonar-tools
# Copyright (C) 2022 Olivier Korach
# mailto:olivier.korach AT gmail DOT com
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
"""Abstraction of the SonarQube "hotspot" concept"""

import json
import re
import requests.utils
import sonar.utilities as util
import sonar.issue_changelog as changelog
from sonar import env, projects, findings, syncer, users


_JSON_FIELDS_REMAPPED = (
    ('pull_request', 'pullRequest'),
    ('_comments', 'comments')
)

_JSON_FIELDS_PRIVATE = ('endpoint', 'id', '_json', '_changelog', 'assignee', 'hash', 'sonarqube',
    'creation_date', 'modification_date', '_debt', 'component', 'language', 'resolution')

_CSV_FIELDS = ('key', 'rule', 'type', 'severity', 'status', 'createdAt', 'updatedAt', 'projectKey', 'projectName',
            'branch', 'pullRequest', 'file', 'line', 'effort', 'message')

_HOTSPOTS = {}


class TooManyHotspotsError(Exception):
    def __init__(self, nbr_issues, message):
        super().__init__()
        self.nbr_issues = nbr_issues
        self.message = message

class Hotspot(findings.Finding):

    def __init__(self, key, endpoint, data=None, from_export=False):
        super().__init__(key, endpoint, data, from_export)
        self.vulnerabilityProbability = None
        self.securityCategory = None
        self.type = 'SECURITY_HOTSPOT'
        self._details = None
        if data is not None:
            self.category = data['securityCategory']
            self.vulnerabilityProbability = data['vulnerabilityProbability']
        # FIXME: Ugly hack to fix how hotspot branches are managed
        m = re.match(r"^(.*):BRANCH:(.*)$", self.projectKey)
        if m:
            self.projectKey = m.group(1)
            self.branch = m.group(2)
        m = re.match(r"^(.*):PULL_REQUEST:(.*)$", self.projectKey)
        if m:
            self.projectKey = m.group(1)
            self.branch = m.group(2)
        _HOTSPOTS[self.uuid()] = self

    def __str__(self):
        return f"Hotspot key '{self.key}'"

    def url(self):
        branch = ''
        if self.branch is not None:
            branch = f'branch={requests.utils.quote(self.branch)}&'
        elif self.pull_request is not None:
            branch = f'pullRequest={requests.utils.quote(self.pull_request)}&'
        return f'{self.endpoint.url}/security_hotspots?{branch}id={self.projectKey}&hotspots={self.key}'

    def to_json(self):
        data = super().to_json()
        data['url'] = self.url()
        return data

    def __mark_as(self, resolution, comment=None):
        params = {'hotspot': self.key, 'status': 'REVIEWED', 'resolution': resolution}
        if comment is not None:
            params['comment'] = comment
        return self.post('hotspots/change_status', params=params)

    def mark_as_safe(self):
        return self.__mark_as('SAFE')

    def mark_as_fixed(self):
        return self.__mark_as('FIXED')

    def mark_as_acknowledged(self):
        if self.endpoint.version() < (9, 4, 0):
            util.logger.warning("Platform version is < 9.4, can't acknowledge %s", str(self))
            return True
        return self.__mark_as('ACKNOWLEDGED')

    def mark_as_to_review(self):
        return self.post('hotspots/change_status', params={'hotspot': self.key, 'status': 'TO_REVIEW'})

    def reopen(self):
        return self.mark_as_to_review()

    def add_comment(self, comment):
        params = {'hotspot': self.key, 'comment': comment}
        return self.post('hotspots/add_comment', params=params)

    def assign(self, assignee, comment=None):
        params = {'hotspot': self.key, 'assignee': assignee}
        if comment is not None:
            params['comment'] = comment
        return self.post('hotspots/assign', params=params)

    def __apply_event(self, event, settings):
        util.logger.debug("Applying event %s", str(event))
        # origin = f"originally by *{event['userName']}* on original branch"
        (event_type, data) = event.changelog_type()
        if event_type == 'HOTSPOT_SAFE':
            self.mark_as_safe()
            # self.add_comment(f"Hotspot review safe {origin}")
        elif event_type == 'HOTSPOT_FIXED':
            self.mark_as_fixed()
            # self.add_comment(f"Hotspot marked as fixed {origin}", settings[SYNC_ADD_COMMENTS])
        elif event_type == 'HOTSPOT_TO_REVIEW':
            self.mark_as_to_review()
            # self.add_comment(f"Hotspot marked as fixed {origin}", settings[SYNC_ADD_COMMENTS])
        elif event_type == 'HOTSPOT_ACKNOWLEDGED':
            self.mark_as_acknowledged()
            # self.add_comment(f"Hotspot marked as acknowledged {origin}", settings[SYNC_ADD_COMMENTS])
        elif event_type == 'ASSIGN':
            if settings[syncer.SYNC_ASSIGN]:
                u = users.get_login_from_name(data, endpoint=self.endpoint)
                if u is None:
                    u = settings[syncer.SYNC_SERVICE_ACCOUNTS][0]
                self.assign(u)
                # self.add_comment(f"Hotspot assigned assigned {origin}", settings[SYNC_ADD_COMMENTS])

        elif event_type == 'INTERNAL':
            util.logger.info("Changelog %s is internal, it will not be applied...", str(event))
            # self.add_comment(f"Change of issue type {origin}", settings[SYNC_ADD_COMMENTS])
        else:
            util.logger.error("Event %s can't be applied", str(event))
            return False
        return True

    def apply_changelog(self, source_hotspot, settings):
        events = source_hotspot.changelog()
        if events is None or not events:
            util.logger.debug("Sibling %s has no changelog, no action taken", str(source_hotspot))
            return False

        change_nbr = 0
        start_change = len(self.changelog()) + 1
        util.logger.debug("Applying changelog of %s to %s, from change %d", str(source_hotspot), str(self), start_change)
        for key in sorted(events.keys()):
            change_nbr += 1
            if change_nbr < start_change:
                util.logger.debug("Skipping change already applied in a previous sync: %s", str(events[key]))
                continue
            self.__apply_event(events[key], settings)

        comments = source_hotspot.comments()
        if len(self.comments()) == 0 and settings[syncer.SYNC_ADD_LINK]:
            util.logger.info("Target %s has 0 comments, adding sync link comment", str(self))
            start_change = 1
            self.add_comment(f"Automatically synchronized from [this original issue]({source_hotspot.url()})")
        else:
            start_change = len(self.comments())
            util.logger.info("Target %s already has %d comments", str(self), start_change)
        util.logger.info("Applying comments of %s to %s, from comment %d",
                         str(source_hotspot), str(self), start_change)
        change_nbr = 0
        for key in sorted(comments.keys()):
            change_nbr += 1
            if change_nbr < start_change:
                util.logger.debug("Skipping comment already applied in a previous sync: %s", str(comments[key]))
                continue
            # origin = f"originally by *{event['userName']}* on original branch"
            self.add_comment(comments[key]['value'])
        return True

    def changelog(self):
        if self._changelog is not None:
            return self._changelog
        resp = self.get('hotspots/show', {'hotspot': self.key})
        self._details = json.loads(resp.text)
        util.json_dump_debug(self._details, f"{str(self)} Details = ")
        self._changelog = {}
        seq = 1
        for l in self._details['changelog']:
            d = changelog.Changelog(l)
            if d.is_technical_change():
                # Skip automatic changelog events generated by SonarSource itself
                util.logger.debug('Changelog is a technical change: %s', str(d))
                continue
            util.json_dump_debug(l, "Changelog item Changelog ADDED = ")
            seq += 1
            self._changelog[f"{d.date()}_{seq:03d}"] = d
        return self._changelog

    def comments(self):
        if self._comments is not None:
            return self._comments
        resp = self.get('hotspots/show', {'hotspot': self.key})
        self._details = json.loads(resp.text)
        util.json_dump_debug(self._details, f"{str(self)} Details = ")
        self._comments = {}
        seq = 0
        for c in self._details['comment']:
            seq += 1
            self._comments[f"{c['createdAt']}_{seq:03d}"] = {'date': c['createdAt'], 'event': 'comment',
                'value': c['markdown'], 'user': c['login'], 'userName': c['login'], 'commentKey': c['key']}
        return self._comments


def search_by_project(project_key, endpoint=None, params=None):
    if params is None:
        new_params = {}
    else:
        new_params = params.copy()
    if project_key is None:
        key_list = projects.search(endpoint).keys()
    else:
        key_list = util.csv_to_list(project_key)
    hotspots = {}
    for k in key_list:
        new_params['projectKey'] = k
        util.logger.debug("Hotspots search by project %s with params %s", k, str(params))
        project_hotspots = search(endpoint=endpoint, params=new_params)
        util.logger.info("Project %s has %d hotspots", k, len(project_hotspots))
        hotspots.update(project_hotspots)
    return hotspots


def search(endpoint=None, page=None, params=None):
    if params is None:
        new_params = {}
    else:
        new_params = params.copy()
    new_params['ps'] = 500
    p = 1
    hotspots = {}
    while True:
        if page is None:
            new_params['p'] = p
        else:
            new_params['p'] = page
        resp = env.get('hotspots/search', params=new_params, ctxt=endpoint)
        data = json.loads(resp.text)
        nbr_hotspots = data['paging']['total']
        nbr_pages = (nbr_hotspots + 499) // 500
        util.logger.debug("Number of issues: %d - Page: %d/%d", nbr_hotspots, new_params['p'], nbr_pages)
        if page is None and nbr_hotspots > 10000:
            raise TooManyHotspotsError(nbr_hotspots,
                                     f'{nbr_hotspots} hotpots returned by api/hotspots/search, '
                                     'this is more than the max 10000 possible')

        for i in data['hotspots']:
            hotspots[i['key']] = get_object(i['key'], endpoint=endpoint, data=i)
        if page is not None or p >= nbr_pages:
            break
        p += 1
    return hotspots


def get_object(key, data=None, endpoint=None, from_export=False):
    if key not in _HOTSPOTS:
        _ = Hotspot(key=key, data=data, endpoint=endpoint, from_export=from_export)
    return _HOTSPOTS[key]
