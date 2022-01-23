#
# sonar-tools
# Copyright (C) 2019-2022 Olivier Korach
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
'''

    Abstraction of the SonarQube System Info File (or Support Info File) concept

'''

import datetime
import re
from dateutil.relativedelta import relativedelta
import sonarqube.utilities as util
import sonarqube.audit_severities as sev
import sonarqube.audit_types as typ
import sonarqube.audit_rules as rules
import sonarqube.audit_problem as pb

_RELEASE_DATE_6_7 = datetime.datetime(2017, 11, 8) + relativedelta(months=+6)
_RELEASE_DATE_7_9 = datetime.datetime(2019, 7, 1) + relativedelta(months=+6)
_RELEASE_DATE_8_9 = datetime.datetime(2021, 5, 4) + relativedelta(months=+6)

_APP_NODES = 'Application Nodes'
_ES_NODES = 'Search Nodes'
_SYSTEM = 'System'
_SETTINGS = 'Settings'
_STATS = 'Statistics'
_STORE_SIZE = 'Store Size'
_ES_STATE = 'Search State'

_JVM_OPTS = ('sonar.{}.javaOpts', 'sonar.{}.javaAdditionalOpts')

_MIN_DATE_LOG4SHELL = datetime.datetime(2021, 12, 1)

class NotSystemInfo(Exception):
    def __init__(self, message):
        super().__init__()
        self.message = message

class Sif:

    def __init__(self, json_sif):
        if not is_sysinfo(json_sif):
            util.logger.critical("Provided JSON does not seem to be a system info")
            raise NotSystemInfo("JSON is not a system info nor a support info")
        self.json_sif = json_sif

    def edition(self):
        return self.json_sif[_STATS]['edition']

    def database(self):
        return self.json_sif[_STATS]['database']['name']

    def plugins(self):
        return self.json_sif[_STATS]['plugins']

    def license_type(self):
        if 'License' not in self.json_sif:
            return None
        elif 'type' in self.json_sif['License']:
            return self.json_sif['License']['type']
        return None

    def version(self, digits=3, as_string=False):
        sif_v = self.__get_field('Version')
        if sif_v is None:
            return None

        split_version = sif_v.split('.')
        if as_string:
            return '.'.join(split_version[0:digits])
        else:
            return tuple(int(n) for n in split_version[0:digits])

    def server_id(self):
        return self.__get_field('Server ID')

    def start_time(self):
        try:
            return util.string_to_date(self.json_sif[_SETTINGS]['sonar.core.startTime']).replace(tzinfo=None)
        except KeyError:
            pass
        try:
            return util.string_to_date(self.json_sif[_SYSTEM]['Start Time']).replace(tzinfo=None)
        except KeyError:
            return None

    def store_size(self, node_id=None):
        setting = None
        if node_id is None:
            if _ES_NODES in self.json_sif:
                node_id = self.__get_first_live_node(_ES_NODES)
                setting = self.json_sif[_ES_NODES][node_id][_ES_STATE][_STORE_SIZE]
            else:
                try:
                    setting = self.json_sif[_ES_STATE][_STORE_SIZE]
                except KeyError:
                    for v in self.json_sif['Elasticsearch']['Nodes'].values():
                        if _STORE_SIZE in v:
                            setting = v[_STORE_SIZE]
                            break
        else:
            setting = self.json_sif[_ES_NODES][node_id][_ES_STATE][_STORE_SIZE]
        if setting is None:
            return None

        (val, unit) = setting.split(' ')
        # For decimal separator in some countries
        val = val.replace(',', '.')
        if unit == 'MB':
            return float(val)
        elif unit == 'GB':
            return float(val) * 1024
        elif unit == 'KB':
            return float(val) / 1024
        return None

    def audit(self):
        util.logger.info("Auditing System Info")
        return (
            self.__audit_version() +
            self.__audit_web_settings() +
            self.__audit_ce_settings() +
            self.__audit_background_tasks() +
            self.__audit_es_settings() +
            self.__audit_dce_settings() +
            self.__audit_jdbc_url() +
            self.__audit_log_level() +
            self.__audit_version()
        )

    def __get_field(self, name, node_type=_APP_NODES):
        if _SYSTEM in self.json_sif and name in self.json_sif[_SYSTEM]:
            return self.json_sif[_SYSTEM][name]
        elif 'SonarQube' in self.json_sif and name in self.json_sif['SonarQube']:
            return self.json_sif['SonarQube'][name]
        elif node_type in self.json_sif:
            for node in self.json_sif[node_type]:
                try:
                    return node[_SYSTEM][name]
                except KeyError:
                    pass
        return None

    def __process_settings(self, process):
        opts = [x.format(process) for x in _JVM_OPTS]
        return self.json_sif[_SETTINGS][opts[1]] + " " + self.json_sif[_SETTINGS][opts[0]]

    def __web_settings(self):
        return self.__process_settings('web')

    def __ce_settings(self):
        return self.__process_settings('ce')

    def __search_settings(self):
        return self.__process_settings('search')

    def __eligible_to_log4shell_check(self):
        st_time = self.start_time()
        if st_time is None:
            return False
        return st_time > _MIN_DATE_LOG4SHELL

    def __audit_log4shell(self, jvm_settings, broken_rule):
        # If SIF is older than 2022 don't audit for log4shell to avoid noise
        if not self.__eligible_to_log4shell_check():
            return []

        util.logger.debug('Auditing log4shell vulnerability fix')
        sq_version = self.version()
        if sq_version < (8, 9, 6) or ((9, 0, 0) <= sq_version < (9, 2, 4)):
            for s in jvm_settings.split(' '):
                if s == '-Dlog4j2.formatMsgNoLookups=true':
                    return []
            rule = rules.get_rule(broken_rule)
            return [pb.Problem(rule.type, rule.severity, rule.msg)]
        return []

    def __audit_jdbc_url(self):
        util.logger.info('Auditing JDBC settings')
        problems = []
        stats = self.json_sif.get(_SETTINGS)
        if stats is None:
            util.logger.error("Can't verify Database settings in System Info File, was it corrupted or redacted ?")
            return problems
        jdbc_url = stats.get('sonar.jdbc.url', None)
        util.logger.debug('JDBC URL = %s', str(jdbc_url))
        if jdbc_url is None:
            rule = rules.get_rule(rules.RuleId.SETTING_JDBC_URL_NOT_SET)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg))
        elif re.search(r':(postgresql://|sqlserver://|oracle:thin:@)(localhost|127\.0+\.0+\.1)[:;/]', jdbc_url):
            lic = self.license_type()
            if lic == 'PRODUCTION':
                rule = rules.get_rule(rules.RuleId.SETTING_DB_ON_SAME_HOST)
                problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(jdbc_url)))
        return problems

    def __audit_dce_settings(self):
        util.logger.info('Auditing DCE settings')
        problems = []
        stats = self.json_sif.get(_STATS)
        if stats is None:
            util.logger.error("Can't verify edition in System Info File, was it corrupted or redacted ?")
            return problems
        sq_edition = stats.get('edition', None)
        if sq_edition is None:
            util.logger.error("Can't verify edition in System Info File, was it corrupted or redacted ?")
            return problems
        if sq_edition != "datacenter":
            util.logger.info('Not a Data Center Edition, skipping DCE checks')
            return problems
        if _APP_NODES not in self.json_sif:
            util.logger.info("Sys Info too old (pre-8.9), can't check plugins")
            return problems
        # Verify that app nodes have the same plugins installed
        appnodes = self.json_sif[_APP_NODES]
        ref_node_id = self.__get_first_live_node()
        ref_plugins = util.json_dump(appnodes[ref_node_id]['Plugins'])
        ref_name = appnodes[ref_node_id]['Name']
        ref_version = appnodes[ref_node_id][_SYSTEM]['Version']
        for node in appnodes:
            node_version = self.__get_field(node, 'Version')
            if node_version is None:
                continue
            if node_version != ref_version:
                rule = rules.get_rule(rules.RuleId.DCE_DIFFERENT_APP_NODES_VERSIONS)
                problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(ref_name, node['Name'])))
            node_plugins = util.json_dump(node['Plugins'])
            if node_plugins != ref_plugins:
                rule = rules.get_rule(rules.RuleId.DCE_DIFFERENT_APP_NODES_PLUGINS)
                problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(ref_name, node['Name'])))
            if not node[_SYSTEM]['Official Distribution']:
                rule = rules.get_rule(rules.RuleId.DCE_APP_NODE_UNOFFICIAL_DISTRO)
                problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(node['Name'])))
            if node['Health'] != "GREEN":
                rule = rules.get_rule(rules.RuleId.DCE_APP_NODE_NOT_GREEN)
                problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(node['Name'], node['Health'])))
        return problems

    def __audit_log_level(self):
        util.logger.debug('Auditing log levels')
        log_level = self.__get_field("Web Logging")
        if log_level is None:
            return []
        log_level = log_level["Logs Level"]
        if log_level not in ("DEBUG", "TRACE"):
            return []
        if log_level == "TRACE":
            return [pb.Problem(typ.Type.PERFORMANCE, sev.Severity.CRITICAL,
                "Log level set to TRACE, this does very negatively affect platform performance, "
                "reverting to INFO is required")]
        if log_level == "DEBUG":
            return [pb.Problem(typ.Type.PERFORMANCE, sev.Severity.HIGH,
                "Log level is set to DEBUG, this may affect platform performance, "
                "reverting to INFO is recommended")]
        return []

    def __audit_version(self):
        st_time = self.start_time()
        sq_version = self.version()
        if ((st_time > _RELEASE_DATE_6_7 and sq_version < (6, 7, 0)) or
            (st_time > _RELEASE_DATE_7_9 and sq_version < (7, 9, 0)) or
            (st_time > _RELEASE_DATE_8_9 and sq_version < (8, 9, 0))):
            rule = rules.get_rule(rules.RuleId.BELOW_LTS)
            return [pb.Problem(rule.type, rule.severity, rule.msg)]
        return []

    def __audit_web_settings(self):
        util.logger.debug('Auditing Web settings')
        problems = []
        web_settings = self.__web_settings()

        web_ram = _get_memory(web_settings)
        if web_ram is None:
            rule = rules.get_rule(rules.RuleId.SETTING_WEB_NO_HEAP)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg))
        elif web_ram < 1024 or web_ram > 2048:
            rule = rules.get_rule(rules.RuleId.SETTING_WEB_HEAP)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(web_ram, 1024, 2048)))
        else:
            util.logger.debug("sonar.web.javaOpts -Xmx memory setting value is %d MB, "
                            "within the recommended range [1024-2048]", web_ram)

        problems += self.__audit_log4shell(web_settings, rules.RuleId.LOG4SHELL_WEB)
        return problems

    def __audit_ce_settings(self):
        util.logger.info('Auditing CE settings')
        problems = []
        ce_settings = self.__ce_settings()
        ce_ram = _get_memory(ce_settings)
        ce_tasks = self.__get_field('Compute Engine Tasks')
        if ce_tasks is None:
            return []
        ce_workers = ce_tasks['Worker Count']
        MAX_WORKERS = 4
        if ce_workers > MAX_WORKERS:
            rule = rules.get_rule(rules.RuleId.SETTING_CE_TOO_MANY_WORKERS)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(ce_workers, MAX_WORKERS)))
        else:
            util.logger.debug("%d CE workers configured, correct compared to the max %d recommended",
                            ce_workers, MAX_WORKERS)

        if ce_ram is None:
            rule = rules.get_rule(rules.RuleId.SETTING_CE_NO_HEAP)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg))
        elif ce_ram < 512 * ce_workers or ce_ram > 2048 * ce_workers:
            rule = rules.get_rule(rules.RuleId.SETTING_CE_HEAP)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(ce_ram, 512, 2048, ce_workers)))
        else:
            util.logger.debug("sonar.ce.javaOpts -Xmx memory setting value is %d MB, "
                            "within recommended range ([512-2048] x %d workers)", ce_ram, ce_workers)

        problems += self.__audit_log4shell(ce_settings, rules.RuleId.LOG4SHELL_CE)
        return problems

    def __audit_background_tasks(self):
        util.logger.debug('Auditing CE background tasks')
        problems = []
        ce_tasks = self.__get_field('Compute Engine Tasks')
        if ce_tasks is None:
            return []
        ce_workers = ce_tasks['Worker Count']
        ce_success = ce_tasks["Processed With Success"]
        ce_error = ce_tasks["Processed With Error"]
        ce_pending = ce_tasks["Pending"]
        if ce_success == 0 and ce_error == 0:
            failure_rate = 0
        else:
            failure_rate = ce_error / (ce_success+ce_error)
        if ce_error > 10 and failure_rate > 0.01:
            rule = rules.get_rule(rules.RuleId.BACKGROUND_TASKS_FAILURE_RATE_HIGH)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(int(failure_rate * 100))))
        else:
            util.logger.debug('Number of failed background tasks (%d), and failure rate %d%% is OK',
                            ce_error, int(failure_rate * 100))

        if ce_pending > 100:
            rule = rules.get_rule(rules.RuleId.BACKGROUND_TASKS_PENDING_QUEUE_VERY_LONG)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(ce_pending)))
        elif ce_pending > 20 and ce_pending > (10*ce_workers):
            rule = rules.get_rule(rules.RuleId.BACKGROUND_TASKS_PENDING_QUEUE_LONG)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(ce_pending)))
        else:
            util.logger.debug('Number of pending background tasks (%d) is OK', ce_pending)
        return problems

    def __audit_es_settings(self):
        util.logger.info('Auditing Search Server settings')
        problems = []
        es_settings = self.__search_settings()
        es_ram = _get_memory(es_settings)
        index_size = self.store_size()

        if es_ram is None:
            rule = rules.get_rule(rules.RuleId.SETTING_ES_NO_HEAP)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg))
        elif index_size is not None and es_ram < 2 * index_size and es_ram < index_size + 1000:
            rule = rules.get_rule(rules.RuleId.SETTING_ES_HEAP)
            problems.append(pb.Problem(rule.type, rule.severity, rule.msg.format(es_ram, index_size)))
        else:
            util.logger.debug("Search server memory %d MB is correct wrt to index size of %d MB", es_ram, index_size)
        problems += self.__audit_log4shell(es_settings, rules.RuleId.LOG4SHELL_ES)
        return problems

    def __get_first_live_node(self, node_type=_APP_NODES):
        #til.logger.debug('Searching LIVE node %s in %s', node_type, util.json_dump(sif))
        if node_type not in self.json_sif:
            return None
        i = 0
        for node in self.json_sif[node_type]:
            if ((node_type == _APP_NODES and _SYSTEM in node) or
                (node_type == _ES_NODES and _ES_STATE in node)):
                return i
            i += 1
        return None


def _get_memory(setting):
    for s in setting.split(' '):
        if re.match('-Xmx', s):
            val = int(s[4:-1])
            unit = s[-1].upper()
            if unit == 'M':
                return val
            elif unit == 'G':
                return val * 1024
            elif unit == 'K':
                return val // 1024
    util.logger.warning("No JVM memory settings specified in %s", setting)
    return None

def is_sysinfo(sysinfo):
    for key in (_SYSTEM, 'Database', _SETTINGS):
        if key not in sysinfo:
            return False
    return True