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
"""

    Abstraction of the SonarQube general object concept

"""

import json
from queue import Queue
from threading import Thread
from sonar import utilities


class SqObject:
    def __init__(self, key, endpoint):
        self.key = key  #: Object unique key
        self.endpoint = endpoint  #: Reference to the SonarQube platform
        self._json = None

    def uuid(self):
        return self.key

    def get_env(self):
        return self.endpoint

    def get(self, api, params=None, exit_on_error=True):
        """Executes and HTTP GET against the SonarQube platform

        :param str api: API to invoke (eg api/issues/search)
        :param dict params: List of parameters to pass to the API
        :param bool exit_on_error: Whether to exit on HTTP error
        :return: The request response
        :rtype: requests.Response
        """
        return self.endpoint.get(api=api, params=params, exit_on_error=exit_on_error)

    def post(self, api, params=None, exit_on_error=True):
        """Executes and HTTP POST against the SonarQube platform

        :param str api: API to invoke (eg api/issues/search)
        :param dict params: List of parameters to pass to the API
        :param bool exit_on_error: Whether to exit on HTTP error
        :return: The request response
        :rtype: requests.Response
        """
        return self.endpoint.post(api=api, params=params, exit_on_error=exit_on_error)

    def delete(self, api, params=None):
        """Executes and HTTP DELETE against the SonarQube platform

        :param str api: API to invoke (eg api/issues/search)
        :param dict params: List of parameters to pass to the API
        :return: The request response
        :rtype: requests.Response
        """
        resp = self.endpoint.delete(api, params)
        return resp.ok


def __search_thread(queue):
    while not queue.empty():
        (endpoint, api, objects, key_field, returned_field, object_class, params, page) = queue.get()
        page_params = params.copy()
        page_params["page"] = page
        utilities.logger.debug("Threaded search: API = %s params = %s", api, str(params))
        data = json.loads(endpoint.get(api, params=params).text)
        for obj in data[returned_field]:
            if object_class.__name__ in ("QualityProfile", "Groups", "Portfolio"):
                objects[obj[key_field]] = object_class.load(endpoint=endpoint, data=obj)
            else:
                objects[obj[key_field]] = object_class(obj[key_field], endpoint, data=obj)
        queue.task_done()


def search_objects(api, endpoint, key_field, returned_field, object_class, params, threads=8):
    """
    :meta private:
    """
    __MAX_SEARCH = 500
    new_params = {} if params is None else params.copy()
    if "ps" not in new_params:
        new_params["ps"] = __MAX_SEARCH
    new_params["p"] = 1
    objects_list = {}
    data = json.loads(endpoint.get(api, params=new_params).text)
    for obj in data[returned_field]:
        if object_class.__name__ in ("Portfolio", "Group", "QualityProfile", "User"):
            objects_list[obj[key_field]] = object_class.load(endpoint=endpoint, data=obj)
        else:
            objects_list[obj[key_field]] = object_class(obj[key_field], endpoint, data=obj)
    nb_pages = utilities.nbr_pages(data)
    if nb_pages == 1:
        return objects_list
    q = Queue(maxsize=0)
    for page in range(2, nb_pages + 1):
        q.put((endpoint, api, objects_list, key_field, returned_field, object_class, params, page))
    for i in range(threads):
        utilities.logger.debug("Starting %s search thread %d", object_class.__name__, i)
        worker = Thread(target=__search_thread, args=[q])
        worker.setDaemon(True)
        worker.start()
    q.join()
    return objects_list
