#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright 2019 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
__metaclass__ = type

from ansible.module_utils.basic import AnsibleModule

import fnmatch
import json
import os
import yaml


ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = """
---
module: container_config_hash
author:
  - "Emilien Macchi (@EmilienM)"
version_added: '2.9'
short_description: Generate config hashes for container startup configs
notes: []
description:
  - Generate config hashes for container startup configs
requirements:
  - None
options:
  check_mode:
    description:
      - Ansible check mode is enabled
    type: bool
    default: False
  config_vol_prefix:
    description:
      - Config volume prefix
    type: str
    default: '/var/lib/config-data'
  debug:
    description:
      - Enable debug
    type: bool
    default: False
  step:
    description:
      - Step number
    default: 6
    type: int
"""

EXAMPLES = """
- name: Update config hashes for container startup configs
  container_config_hash:
"""

CONTAINER_STARTUP_CONFIG = '/var/lib/edpm-config/container-startup-config'


class ContainerConfigHashManager:
    """Notes about this module.

    It will generate container config hash that will be consumed by
    the edpm-container-manage role that is using podman_container module.
    """

    def __init__(self, module, results):

        super(ContainerConfigHashManager, self).__init__()
        self.module = module
        self.results = results

        # parse args
        args = self.module.params

        # Set parameters
        self.config_vol_prefix = args['config_vol_prefix']

        # Update container-startup-config with new config hashes
        self._update_hashes()

        self.module.exit_json(**self.results)

    def _remove_file(self, path):
        """Remove a file.

        :param path: string
        """
        if os.path.exists(path):
            os.remove(path)

    def _find(self, path, pattern='*.json'):
        """Returns a list of files in a directory.

        :param path: string
        :param pattern: string
        :returns: list
        """
        configs = []
        if os.path.exists(path):
            for root, dirnames, filenames in os.walk(path):
                for filename in fnmatch.filter(filenames, pattern):
                    configs.append(os.path.join(root, filename))
        else:
            self.module.warn('{} does not exists'.format(path))
        return configs

    def _slurp(self, path):
        """Slurps a file and return its content.

        :param path: string
        :returns: string
        """
        if os.path.exists(path):
            with open(path, 'r') as f:
                return f.read()
        else:
            self.module.warn('{} was not found.'.format(path))
            return ''

    def _update_container_config(self, path, config):
        """Update a container config.

        :param path: string
        :param config: string
        """
        with open(path, 'wb') as f:
            f.write(json.dumps(config, indent=2).encode('utf-8'))
        os.chmod(path, 0o600)
        self.results['changed'] = True

    def _get_config_hash(self, config_volume):
        """Returns a config hash from a config_volume.

        :param config_volume: string
        :returns: string
        """
        hashfile = "%s.md5sum" % config_volume
        if os.path.exists(hashfile):
            return self._slurp(hashfile).strip('\n')

    def _get_config_base(self, prefix, volume):
        """Returns a config base path for a specific volume.

        :param prefix: string
        :param volume: string
        :returns: string
        """
        # crawl the volume's path upwards until we find the
        # volume's base, where the hashed config file resides
        path = volume
        base = prefix.rstrip(os.path.sep)
        base_generated = os.path.join(base, 'ansible-generated')
        while path.startswith(prefix):
            dirname = os.path.dirname(path)
            if dirname == base or dirname == base_generated:
                return path
            else:
                path = dirname
        self.module.fail_json(
            msg='Could not find config base for: {} '
                'with prefix: {}'.format(volume, prefix))

    def _match_config_volumes(self, config):
        """Return a list of volumes that match a config.

        :param config: dict
        :returns: list
        """
        # Match the mounted config volumes - we can't just use the
        # key as e.g "novacomute" consumes config-data/nova
        prefix = self.config_vol_prefix
        try:
            volumes = config.get('volumes', [])
        except AttributeError:
            self.module.fail_json(
                msg='Error fetching volumes. Prefix: '
                    '{} - Config: {}'.format(prefix, config))
        return sorted([self._get_config_base(prefix, v.split(":")[0])
                       for v in volumes if v.startswith(prefix)])

    def _update_hashes(self):
        """Update container startup config with new config hashes if needed.
        """
        configs = self._find(CONTAINER_STARTUP_CONFIG)
        for config in configs:
            old_config_hash = ''
            cname = os.path.splitext(os.path.basename(config))[0]
            if cname.startswith('hashed-'):
                # Take the opportunity to cleanup old hashed files which
                # don't exist anymore.
                self._remove_file(config)
                continue
            startup_config_json = json.loads(self._slurp(config))
            config_volumes = self._match_config_volumes(startup_config_json)
            config_hashes = [
                self._get_config_hash(vol_path) for vol_path in config_volumes
            ]
            config_hashes = filter(None, config_hashes)
            if 'environment' in startup_config_json:
                old_config_hash = startup_config_json['environment'].get(
                    'EDPM_CONFIG_HASH', '')
            if config_hashes is not None and config_hashes:
                config_hash = '-'.join(config_hashes)
                if config_hash == old_config_hash:
                    # config doesn't need an update
                    continue
                self.module.debug(
                    'Config change detected for {}, new hash: {}'.format(
                        cname,
                        config_hash
                    )
                )
                if 'environment' not in startup_config_json:
                    startup_config_json['environment'] = {}
                startup_config_json['environment']['EDPM_CONFIG_HASH'] = (
                    config_hash)
                self._update_container_config(config, startup_config_json)


def main():
    module = AnsibleModule(
        argument_spec=yaml.safe_load(DOCUMENTATION)['options'],
        supports_check_mode=True,
    )
    results = dict(
        changed=False
    )
    ContainerConfigHashManager(module, results)


if __name__ == '__main__':
    main()
