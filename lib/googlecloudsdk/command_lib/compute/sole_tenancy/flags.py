# Copyright 2018 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Flags for the `compute sole-tenancy` related commands."""
from googlecloudsdk.calliope import actions
from googlecloudsdk.calliope import arg_parsers


def AddNodeAffinityFlagToParser(parser):
  """Adds a node affinity flag used for scheduling instances."""
  sole_tenancy_group = parser.add_group('Sole Tenancy', mutex=True, hidden=True)
  sole_tenancy_group.add_argument(
      '--node-affinity-file',
      type=arg_parsers.BufferedFileInput(),
      help="""\
          The JSON/YAML file containing the configuration of desired nodes onto
          which this instance could be scheduled. These rules filter the nodes
          according to their node affinity labels. A node's affinity labels come
          from the node template of the group the node is in.

          The file should contain a list of a JSON/YAML objects with the
          following fields:

          *key*::: Corresponds to the node affinity label keys of
          the Node resource.
          *operator*::: Specifies the node selection type. Must be one of:
            `IN`: Requires Compute Engine to seek for matched nodes.
            `NOT_IN`: Requires Compute Engine to avoid certain nodes.
          *values*::: Optional. A list of values which correspond to the node
          affinity label values of the Node resource.
          """)
  node_shortcuts_group = sole_tenancy_group.add_group('Node Groups')
  node_shortcuts_group.add_argument(
      '--node-group',
      required=True,
      help='The name of the node group to schedule this instance on.')
  node_shortcuts_group.add_argument(
      '--node-index',
      type=int,
      help='The index of the node within the node group to schedule this '
           'instance on.')


def AddSoleTenancyArgsToParser(parser):
  parser.add_argument(
      '--sole-tenancy-host',
      action=actions.DeprecationAction(
          '--sole-tenancy-host',
          removed=True,
          error='Instance creation on sole tenancy hosts is deprecated. Use '
                '--node-affinity-file, --node-group, --node-index flags '
                'instead. See `alpha compute sole-tenancy node-groups` for '
                'more information.'),
      hidden=True,
      help='The name of the sole tenancy host to create this instance on.')
