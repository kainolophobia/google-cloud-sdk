# Copyright 2016 Google Inc. All Rights Reserved.
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

"""Debug apis layer."""

import re
import urllib

from googlecloudsdk.api_lib.debug import errors
from googlecloudsdk.api_lib.projects import util as project_util
from googlecloudsdk.core import apis
from googlecloudsdk.core import config
from googlecloudsdk.core.util import retry

# Names for default module and version. In App Engine, the default module and
# version don't report explicit names to the debugger, so use these strings
# instead when displaying the target name. Note that this code assumes there
# will not be a non-default version or module explicitly named 'default', since
# that would result in a naming conflict between the actual default and the
# one named 'default'.
DEFAULT_MODULE = 'default'
DEFAULT_VERSION = 'default'

# Currently, Breakpoint IDs are generated using three hex encoded numbers,
# separated by '-'. The first is always 13-16 digits, the second is always
# exactly 4 digits, and the third can be up to 8 digits.
_BREAKPOINT_ID_PATTERN = re.compile(r'^[0-9a-f]{13,16}-[0-9a-f]{4}-[0-9a-f]+$')


def SplitLogExpressions(format_string):
  """Extracts {expression} substrings into a separate array.

  Each substring of the form {expression} will be extracted into an array, and
  each {expression} substring will be replaced with $N, where N is the index
  of the extraced expression in the array.

  For example, given the input:
    'a={a}, b={b}'
   The return value would be:
    ('a=$0, b=$1', ['a', 'b'])

  Args:
    format_string: The string to process.
  Returns:
    string, [string] - The new format string and the array of expressions.
  Raises:
    ValueError: if the string has unbalanced braces.
  """
  expressions = []
  log_format = ''
  current_expression = ''
  brace_count = 0
  need_separator = False
  for c in format_string:
    if need_separator and c.isdigit():
      log_format += ' '
    need_separator = False
    if c == '{':
      if brace_count:
        # Nested braces
        current_expression += c
      else:
        # New expression
        current_expression = ''
      brace_count += 1
    elif brace_count:
      # Currently reading an expression.
      if c != '}':
        current_expression += c
        continue
      brace_count -= 1
      if brace_count == 0:
        # Finish processing the expression
        brace_count = 0
        if current_expression in expressions:
          i = expressions.index(current_expression)
        else:
          i = len(expressions)
          expressions.append(current_expression)
        log_format += '${0}'.format(i)
        # If the next character is a digit, we need an extra space to prevent
        # the agent from combining the positional argument with the subsequent
        # digits.
        need_separator = True
      else:
        # Closing a nested brace
        current_expression += c
    else:
      # Not in or starting an expression.
      log_format += c
  if brace_count:
    # Unbalanced left brace.
    raise ValueError('Too many "{" characters in format string')
  return log_format, expressions


def MergeLogExpressions(log_format, expressions):
  """Replaces each $N substring with the corresponding {expression}.

  This function is intended for reconstructing an input expression string that
  has been split using SplitLogExpressions. It is not intended for substituting
  the expression results at log time.

  Args:
    log_format: A string containing 0 or more $N substrings, where N is any
      valid index into the expressions array. Each such substring will be
      replaced by '{expression}', where "expression" is expressions[N].
    expressions: The expressions to substitute into the format string.
  Returns:
    The combined string.
  """
  return re.sub(r'\$([0-9]+)', r'{{{\1}}}', log_format).format(*expressions)


def DebugViewUrl(breakpoint):
  """Returns a URL to view a breakpoint in the browser.

  Given a breakpoint, this transform will return a URL which will open the
  snapshot's location in a debug view pointing at the snapshot.

  Args:
    breakpoint: A breakpoint object with added information on project and
    debug target.
  Returns:
    The URL for the breakpoint.
  """
  debug_view_url = 'https://console.developers.google.com/debug/fromgcloud?'
  data = [
      ('project', breakpoint.project_number),
      ('dbgee', breakpoint.target_uniquifier),
      ('bp', breakpoint.id)
  ]
  location = breakpoint.location
  if location:
    file_path = location.path
    if file_path:
      data.append(('fp', file_path))
    line = location.line
    if line:
      data.append(('fl', line))
  return debug_view_url + urllib.urlencode(data)


class DebugObject(object):
  """Base class for debug api wrappers."""
  _debug_client = None
  _debug_messages = None
  _resource_client = None
  _resource_messages = None
  _project_id_to_number_cache = {}
  _project_number_to_id_cache = {}

  # Breakpoint type constants (initialized by IntializeApiClients)
  SNAPSHOT_TYPE = None
  LOGPOINT_TYPE = None

  CLIENT_VERSION = 'google.com/gcloud/{0}'.format(config.CLOUD_SDK_VERSION)

  @classmethod
  def _CheckClient(cls):
    if (not cls._debug_client or not cls._debug_messages or
        not cls._resource_client or not cls._resource_messages):
      raise errors.NoEndpointError()

  @classmethod
  def InitializeApiClients(cls, http):
    cls._debug_client = apis.GetClientInstance('debug', 'v2', http)
    cls._debug_messages = apis.GetMessagesModule('debug', 'v2')
    cls._resource_client = apis.GetClientInstance('projects', 'v1beta1', http)
    cls._resource_messages = apis.GetMessagesModule('projects', 'v1beta1')
    cls.SNAPSHOT_TYPE = (
        cls._debug_messages.Breakpoint.ActionValueValuesEnum.CAPTURE)
    cls.LOGPOINT_TYPE = cls._debug_messages.Breakpoint.ActionValueValuesEnum.LOG

  @classmethod
  def InitializeClientVersion(cls, version):
    cls.CLIENT_VERSION = version

  @classmethod
  def GetProjectNumber(cls, project_id):
    """Retrieves the project number given a project ID.

    Args:
      project_id: The ID of the project.
    Returns:
      Integer project number.
    """
    if project_id in cls._project_id_to_number_cache:
      return cls._project_id_to_number_cache[project_id]
    # Convert errors in the client API to something meaningful.
    @project_util.HandleHttpError
    def GetProject(message):
      return cls._resource_client.projects.Get(message)
    project = GetProject(
        cls._resource_messages.CloudresourcemanagerProjectsGetRequest(
            projectId=project_id))
    cls._project_id_to_number_cache[project.projectId] = project.projectNumber
    cls._project_number_to_id_cache[project.projectNumber] = project.projectId
    return project.projectNumber

  @classmethod
  def GetProjectId(cls, project_number):
    """Retrieves the project ID given a project number.

    Args:
      project_number: (int) The unique number of the project.
    Returns:
      Project ID string.
    """
    if project_number in cls._project_number_to_id_cache:
      return cls._project_number_to_id_cache[project_number]
    # Treat the number as an ID to populate the cache. They're interchangeable
    # in lookup.
    cls.GetProjectNumber(str(project_number))
    return cls._project_number_to_id_cache.get(project_number,
                                               str(project_number))


class Debugger(DebugObject):
  """Abstracts Cloud Debugger service for a project."""

  def __init__(self, project_id):
    self._CheckClient()
    self._project_id = project_id
    self._project_number = self.GetProjectNumber(project_id)

  @errors.HandleHttpError
  def ListDebuggees(self, include_inactive=False):
    """Lists all debug targets registered with the debug service.

    Args:
      include_inactive: If true, also include debuggees that are not currently
        running.
    Returns:
      [Debuggee] A list of debuggees.
    """
    request = self._debug_messages.ClouddebuggerDebuggerDebuggeesListRequest(
        project=str(self._project_number), includeInactive=include_inactive,
        clientVersion=self.CLIENT_VERSION)
    response = self._debug_client.debugger_debuggees.List(request)
    return [Debuggee(debuggee) for debuggee in response.debuggees]

  def DefaultDebuggee(self):
    """Find the default debuggee.

    Returns:
      The Debuggee for the default module and version, if there is exactly one
      such.
    Raises:
      errors.NoDebuggeeError if the default can't be determined.
    """
    debuggees = self.ListDebuggees()
    if len(debuggees) == 1:
      # Just one possible target
      return debuggees[0]
    if not debuggees:
      raise errors.NoDebuggeeError()
    # If they're multiple minor versions of a single version (which we
    # assume to be the default), return the highest-numbered version.
    latest = _FindLatestMinorVersion(debuggees)
    if latest:
      return latest

    # Find all versions of the default module
    by_module = [d for d in debuggees if not d.module]
    if not by_module:
      # No default module. Can't determine the default target.
      raise errors.MultipleDebuggeesError(None, debuggees)
    if len(by_module) == 1:
      return by_module[0]
    # If there are multiple minor versions of a single version of the
    # default module, return the highest-numbered version.
    latest = _FindLatestMinorVersion(by_module)
    if latest:
      return latest

    # The default module may have multiple versions. Choose the default, if it
    # can be determined.
    by_version = [d for d in by_module if not d.version]
    if len(by_version) == 1:
      return by_version[0]
    if not by_version:
      # No default version. Can't determine the default target.
      raise errors.MultipleDebuggeesError(None, debuggees)
    # If there are multiple minor versions of the default version of the
    # default module, return the highest-numbered version.
    latest = _FindLatestMinorVersion(by_version)
    if latest:
      return latest

    # Could not find default version. Note that in the case where the debuggee
    # is an App Engine module version, it is possible to query the module and
    # find the name of the default version. That information is not visible in
    # the corresponding Debuggee object, unfortunately.
    raise errors.NoDebuggeeError()

  def FindDebuggee(self, pattern=None):
    """Find the unique debuggee matching the given pattern.

    Args:
      pattern: A string containing a debuggee ID or a regular expression that
        matches a single debuggee's name or description. If it matches any
        debuggee name, the description will not be inspected.
    Returns:
      The matching Debuggee.
    Raises:
      errors.MultipleDebuggeesError if the pattern matches multiple debuggees.
      errors.NoDebuggeeError if the pattern matches no debuggees.
    """
    if not pattern:
      return self.DefaultDebuggee()
    all_debuggees = self.ListDebuggees()
    match_re = re.compile(pattern)
    debuggees = [d for d in all_debuggees
                 if d.target_id == pattern or match_re.search(d.name)]
    if not debuggees:
      # Try matching on description.
      debuggees = [d for d in all_debuggees
                   if match_re.search(d.description)]
    if len(debuggees) == 1:
      # Just one possible target
      return debuggees[0]
    if not debuggees:
      raise errors.NoDebuggeeError(pattern)

    # Multiple possibilities. Find the latest minor version, if they all
    # point to the same module and version.
    best = _FindLatestMinorVersion(debuggees)
    if not best:
      raise errors.MultipleDebuggeesError(pattern, debuggees)
    return best

  def RegisterDebuggee(self, description, uniquifier, agent_version=None):
    """Register a debuggee with the Cloud Debugger.

    This method is primarily intended to simplify testing, since it registering
    a debuggee is only a small part of the functionality of a debug agent, and
    the rest of the API is not supported here.
    Args:
      description: A concise description of the debuggee.
      uniquifier: A string uniquely identifying the debug target. Note that the
        uniquifier distinguishes between different deployments of a service,
        not between different replicas of a single deployment. I.e., all
        replicas of a single deployment should report the same uniquifier.
      agent_version: A string describing the program registering the debuggee.
        Defaults to "google.com/gcloud/NNN" where NNN is the gcloud version.
    Returns:
      The registered Debuggee.
    """
    if not agent_version:
      agent_version = self.CLIENT_VERSION
    request = self._debug_messages.RegisterDebuggeeRequest(
        debuggee=self._debug_messages.Debuggee(
            project=str(self._project_number), description=description,
            uniquifier=uniquifier, agentVersion=agent_version))
    response = self._debug_client.controller_debuggees.Register(request)
    return Debuggee(response.debuggee)


class Debuggee(DebugObject):
  """Represents a single debuggee."""

  def __init__(self, message):
    self.project_number = int(message.project)
    self.project_id = self.GetProjectId(self.project_number)
    self.agent_version = message.agentVersion
    self.description = message.description
    self.ext_source_contexts = message.extSourceContexts
    self.target_id = message.id
    self.is_disabled = message.isDisabled
    self.is_inactive = message.isInactive
    self.source_contexts = message.sourceContexts
    self.status = message.status
    self.target_uniquifier = message.uniquifier
    self.labels = {}
    if message.labels:
      for l in message.labels.additionalProperties:
        self.labels[l.key] = l.value

  def __eq__(self, other):
    return (isinstance(other, self.__class__) and
            self.target_id == other.target_id)

  def __ne__(self, other):
    return not self.__eq__(other)

  def __repr__(self):
    return '<id={0}, name={1}{2}>'.format(
        self.target_id, self.name, ', description={0}'.format(self.description)
        if self.description else '')

  @property
  def module(self):
    return self.labels.get('module', None)

  @property
  def version(self):
    return self.labels.get('version', None)

  @property
  def minorversion(self):
    return self.labels.get('minorversion', None)

  @property
  def name(self):
    module = self.module
    version = self.version
    if self.module or self.version or self.minorversion:
      return (module or DEFAULT_MODULE) + '-' + (version or DEFAULT_VERSION)
    return self.target_id

  @errors.HandleHttpError
  def GetBreakpoint(self, breakpoint_id):
    """Gets the details for a breakpoint.

    Args:
      breakpoint_id: A breakpoint ID.
    Returns:
      The full Breakpoint message for the ID.
    """
    request = (self._debug_messages.
               ClouddebuggerDebuggerDebuggeesBreakpointsGetRequest(
                   breakpointId=breakpoint_id, debuggeeId=self.target_id,
                   clientVersion=self.CLIENT_VERSION))
    response = self._debug_client.debugger_debuggees_breakpoints.Get(request)
    return self.AddTargetInfo(response.breakpoint)

  @errors.HandleHttpError
  def DeleteBreakpoint(self, breakpoint_id):
    """Deletes a breakpoint.

    Args:
      breakpoint_id: A breakpoint ID.
    """
    request = (self._debug_messages.
               ClouddebuggerDebuggerDebuggeesBreakpointsDeleteRequest(
                   breakpointId=breakpoint_id, debuggeeId=self.target_id,
                   clientVersion=self.CLIENT_VERSION))
    self._debug_client.debugger_debuggees_breakpoints.Delete(request)

  @errors.HandleHttpError
  def ListBreakpoints(self, include_all_users=False, include_inactive=False,
                      restrict_to_type=None):
    self._CheckClient()
    request = (self._debug_messages.
               ClouddebuggerDebuggerDebuggeesBreakpointsListRequest(
                   debuggeeId=self.target_id,
                   includeAllUsers=include_all_users,
                   includeInactive=include_inactive,
                   clientVersion=self.CLIENT_VERSION))
    response = self._debug_client.debugger_debuggees_breakpoints.List(request)
    return self._FilteredDictListWithInfo(response.breakpoints,
                                          restrict_to_type)

  @errors.HandleHttpError
  def ListMatchingBreakpoints(self, location_regexp_or_ids,
                              include_all_users=False, include_inactive=False,
                              restrict_to_type=None):
    """Returns all breakpoints matching the given IDs or patterns.

    Lists all breakpoints for this debuggee, and returns every breakpoint
    where the location field contains the given pattern or the ID is exactly
    equal to the pattern (there can be at most one breakpoint matching by ID).

    Args:
      location_regexp_or_ids: A list of regular expressions or breakpoint IDs.
        Regular expressions will be compared against the location ('path:line')
        of the breakpoints. Exact breakpoint IDs will be retrieved regardless
        of the include_all_users or include_inactive flags.
      include_all_users: If true, search breakpoints created by all users.
      include_inactive: If true, search breakpoints that are in the final state.
      restrict_to_type: An optional breakpoint type (LOGPOINT_TYPE or
        SNAPSHOT_TYPE)
    Returns:
      A list of all matching breakpoints.
    """
    self._CheckClient()
    ids = set([i for i in location_regexp_or_ids
               if _BREAKPOINT_ID_PATTERN.match(i)])
    patterns = [re.compile(p) for p in set(location_regexp_or_ids) - ids]
    request = (self._debug_messages.
               ClouddebuggerDebuggerDebuggeesBreakpointsListRequest(
                   debuggeeId=self.target_id,
                   includeAllUsers=include_all_users,
                   includeInactive=include_inactive,
                   clientVersion=self.CLIENT_VERSION))
    response = self._debug_client.debugger_debuggees_breakpoints.List(request)
    result = [bp for bp in response.breakpoints
              if _MatchesIdOrRegexp(bp, ids, patterns)]
    missing_ids = ids - set([bp.id for bp in result])
    for i in missing_ids:
      result.append(self.GetBreakpoint(i))
    return self._FilteredDictListWithInfo(result, restrict_to_type)

  @errors.HandleHttpError
  def CreateSnapshot(self, location, condition=None, expressions=None,
                     user_email=None, labels=None):
    """Creates a "snapshot" breakpoint.

    Args:
      location: The breakpoint source location, which will be interpreted by
        the debug agents on the machines running the Debuggee. Usually of the
        form file:line-number
      condition: An optional conditional expression in the target's programming
        language. The snapshot will be taken when the expression is true.
      expressions: A list of expressions to evaluate when the snapshot is
        taken.
      user_email: The email of the user who created the snapshot.
      labels: A dictionary containing key-value pairs which will be stored
        with the snapshot definition and reported when the snapshot is queried.
    Returns:
      The created Breakpoint message.
    """
    self._CheckClient()
    labels_value = None
    if labels:
      labels_value = self._debug_messages.Breakpoint.LabelsValue(
          additionalProperties=[
              self._debug_messages.Breakpoint.LabelsValue.AdditionalProperty(
                  key=key, value=value)
              for key, value in labels.iteritems()])
    location = self._LocationFromString(location)
    if not expressions:
      expressions = []
    request = (
        self._debug_messages.
        ClouddebuggerDebuggerDebuggeesBreakpointsSetRequest(
            debuggeeId=self.target_id,
            breakpoint=self._debug_messages.Breakpoint(
                location=location, condition=condition, expressions=expressions,
                labels=labels_value, userEmail=user_email,
                action=(self._debug_messages.Breakpoint.
                        ActionValueValuesEnum.CAPTURE)),
            clientVersion=self.CLIENT_VERSION))
    response = self._debug_client.debugger_debuggees_breakpoints.Set(request)
    return self.AddTargetInfo(response.breakpoint)

  @errors.HandleHttpError
  def CreateLogpoint(self, location, log_format_string, log_level=None,
                     condition=None, user_email=None, labels=None):
    """Creates a logpoint in the debuggee.

    Args:
      location: The breakpoint source location, which will be interpreted by
        the debug agents on the machines running the Debuggee. Usually of the
        form file:line-number
      log_format_string: The message to log, optionally containin {expression}-
        style formatting.
      log_level: String (case-insensitive), one of 'info', 'warning', or
        'error', indicating the log level that should be used for logging.
      condition: An optional conditional expression in the target's programming
        language. The snapshot will be taken when the expression is true.
      user_email: The email of the user who created the snapshot.
      labels: A dictionary containing key-value pairs which will be stored
        with the snapshot definition and reported when the snapshot is queried.
    Returns:
      The created Breakpoint message.
    Raises:
      ValueError: if location or log_format is empty or malformed.
    """
    self._CheckClient()
    if not location:
      raise ValueError('The location must not be empty.')
    if not log_format_string:
      raise ValueError('The log format string must not be empty.')
    labels_value = None
    if labels:
      labels_value = self._debug_messages.Breakpoint.LabelsValue(
          additionalProperties=[
              self._debug_messages.Breakpoint.LabelsValue.AdditionalProperty(
                  key=key, value=value)
              for key, value in labels.iteritems()])
    location = self._LocationFromString(location)
    if log_level:
      log_level = (
          self._debug_messages.Breakpoint.LogLevelValueValuesEnum(
              log_level.upper()))
    log_message_format, expressions = SplitLogExpressions(log_format_string)
    request = (
        self._debug_messages.
        ClouddebuggerDebuggerDebuggeesBreakpointsSetRequest(
            debuggeeId=self.target_id,
            breakpoint=self._debug_messages.Breakpoint(
                location=location, condition=condition, logLevel=log_level,
                logMessageFormat=log_message_format, expressions=expressions,
                labels=labels_value, userEmail=user_email,
                action=(self._debug_messages.Breakpoint.
                        ActionValueValuesEnum.LOG)),
            clientVersion=self.CLIENT_VERSION))
    response = self._debug_client.debugger_debuggees_breakpoints.Set(request)
    return self.AddTargetInfo(response.breakpoint)

  @errors.HandleHttpError
  def WaitForBreakpoint(self, breakpoint_id, timeout=None, retry_ms=500):
    """Waits for a breakpoint to be completed.

    Args:
      breakpoint_id: A breakpoint ID.
      timeout: The number of seconds to wait for completion.
      retry_ms: Milliseconds to wait betweeen retries.
    Returns:
      The Breakpoint message, or None if the breakpoint did not complete before
      the timeout,
    """
    retryer = retry.Retryer(
        max_wait_ms=1000*timeout if timeout is not None else None,
        wait_ceiling_ms=1000)
    request = (self._debug_messages.
               ClouddebuggerDebuggerDebuggeesBreakpointsGetRequest(
                   breakpointId=breakpoint_id, debuggeeId=self.target_id,
                   clientVersion=self.CLIENT_VERSION))
    try:
      result = retryer.RetryOnResult(
          self._debug_client.debugger_debuggees_breakpoints.Get, [request],
          should_retry_if=lambda r, _: not r.breakpoint.isFinalState,
          sleep_ms=retry_ms)
    except retry.RetryException:
      # Timeout before the beakpoint was finalized.
      return None
    return self.AddTargetInfo(result.breakpoint)

  def AddTargetInfo(self, message):
    """Converts a message into an object with added debuggee information.

    Args:
      message: A message returned from a debug API call.
    Returns:
      An object including the fields of the original object plus the following
      fields: project_id, project_number, target_uniquifier, and target_id.
    """
    result = _MessageDict(message, hidden_fields={
        'project_id': self.project_id,
        'project_number': self.project_number,
        'target_uniquifier': self.target_uniquifier,
        'target_id': self.target_id})
    result['consoleViewUrl'] = DebugViewUrl(result)

    # Reformat a few fields for readability
    if message.location:
      result['location'] = '{0}:{1}'.format(message.location.path,
                                            message.location.line)
    if message.logMessageFormat:
      fmt = re.sub(r'\$([0-9]+)', r'{{{\1}}}', message.logMessageFormat)
      result['logMessageFormat'] = fmt.format(*message.expressions)
      result.HideExistingField('expressions')
    return result

  def _LocationFromString(self, location):
    """Converts a file:line location string into a SourceLocation.

    Args:
      location: A string of the form file:line.
    Returns:
      The corresponding SourceLocation message.
    Raises:
      ValueError: if the line is not of the form path:line
    """
    components = location.split(':')
    if len(components) != 2:
      raise ValueError('Location must be of the form "path:line"')
    return self._debug_messages.SourceLocation(path=components[0],
                                               line=int(components[1]))

  def _FilteredDictListWithInfo(self, result, restrict_to_type):
    """Filters a result list to contain only breakpoints of the given type.

    Args:
      result: A list of breakpoint messages, to be filtered.
      restrict_to_type: An optional breakpoint type. If None, no filtering
        will be done.
    Returns:
      The filtered result, converted to equivalent dicts with debug info fields
      added.
    """
    return [self.AddTargetInfo(r) for r in result
            if not restrict_to_type or r.action == restrict_to_type
            or (not r.action and restrict_to_type == self.SNAPSHOT_TYPE)]


def _MatchesIdOrRegexp(snapshot, ids, patterns):
  """Check if a snapshot matches any of the given IDs or regexps.

  Args:
    snapshot: Any _debug_messages.Breakpoint message object.
    ids: A set of strings to search for exact matches on snapshot ID.
    patterns: A list of regular expressions to match against the file:line
      location of the snapshot.
  Returns:
    True if the snapshot matches any ID or pattern.
  """
  if snapshot.id in ids:
    return True
  if not snapshot.location:
    return False
  location = '{0}:{1}'.format(snapshot.location.path, snapshot.location.line)
  for p in patterns:
    if p.search(location):
      return True
  return False


def _FindLatestMinorVersion(debuggees):
  """Given a list of debuggees, find the one with the highest minor version.

  Args:
    debuggees: A list of Debuggee objects.
  Returns:
    If all debuggees have the same name, return the one with the highest
    integer value in its 'minorversion' label. If any member of the list does
    not have a minor version, or if elements of the list have different
    names, returns None.
  """
  if not debuggees:
    return None
  best = None
  best_version = None
  name = None
  for d in debuggees:
    if not name:
      name = d.name
    elif name != d.name:
      return None
    minor_version = d.labels.get('minorversion', 0)
    if not minor_version:
      return None
    minor_version = int(minor_version)
    if not best_version or minor_version > best_version:
      best_version = minor_version
      best = d
  return best


class _MessageDict(dict):
  """An extensible wrapper around message data.

  Fields can be added as dictionary items and retrieved as attributes.
  """

  def __init__(self, message, hidden_fields=None):
    super(_MessageDict, self).__init__()
    self._orig_type = type(message).__name__
    if hidden_fields:
      self._hidden_fields = hidden_fields
    else:
      self._hidden_fields = {}
    for field in message.all_fields():
      value = getattr(message, field.name)
      if not value:
        self._hidden_fields[field.name] = value
      else:
        self[field.name] = value

  def __getattr__(self, attr):
    if attr in self:
      return self[attr]
    if attr in self._hidden_fields:
      return self._hidden_fields[attr]
    raise AttributeError('Type "{0}" does not have attribute "{1}"'.format(
        self._orig_type, attr))

  def HideExistingField(self, field_name):
    if field_name in self._hidden_fields:
      return
    self._hidden_fields[field_name] = self.pop(field_name, None)
