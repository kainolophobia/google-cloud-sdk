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

"""A command to install Application Default Credentials using a user account."""

import textwrap

from googlecloudsdk.api_lib.auth import util as auth_util
from googlecloudsdk.calliope import arg_parsers
from googlecloudsdk.calliope import base
from googlecloudsdk.core import log
from googlecloudsdk.core.console import console_io
from googlecloudsdk.core.credentials import gce as c_gce
from oauth2client import client


class Login(base.Command):
  """Authorize Application Default Credentials to access Google Cloud Platform.

  Obtains user access credentials via a web flow for the Google Cloud Platform
  resources of the given account, and puts them in the well-known location for
  Application Default Credentials to use them as a proxy for a service account.

  This command is useful for when you are developing code that would normally
  use a service account, but you need to run the code in a local development
  environment, and it is easier to provide user credentials.
  These user credentials will then be used as a "proxy" for an associated
  service account. The credentials will apply to all projects accessed through
  Application Default Credentials. This command has no effect on the user
  account(s) used with the `gcloud auth login` command, but any existing
  credentials previously installed by this command (or
  `gcloud auth application-default activate-service-account`) will be
  overwritten.
  """

  @staticmethod
  def Args(parser):
    """Set args for gcloud auth application-default login."""

    parser.add_argument(
        '--launch-browser',
        action='store_true',
        default=True,
        help='Launch a browser for authorization. If not enabled or DISPLAY '
        'variable is not set, prints a URL to standard output to be copied.')
    parser.add_argument(
        '--client-id-file',
        help='A file containing your own client id to use to login.')
    parser.add_argument(
        '--scopes',
        type=arg_parsers.ArgList(min_length=1),
        help='The names of the scopes to authorize for. A list of scopes is '
        'located at '
        '[](https://developers.google.com/identity/protocols/googlescopes).')

  def Format(self, unused_args):
    return None

  def Run(self, args):
    """Run the authentication command."""

    if c_gce.Metadata().connected:
      message = textwrap.dedent("""
          You are running on a Google Compute Engine virtual machine.
          The service credentials associated with this virtual machine
          will automatically be used by Application Default
          Credentials, so it is not necessary to use this command.

          If you decide to proceed anyway, your user credentials may be visible
          to others with access to this virtual machine. Are you sure you want
          to authenticate with your personal account?
          """)
      console_io.PromptContinue(
          message=message, throw_if_unattended=True, cancel_on_no=True)

    override_file = auth_util.AdcEnvVariable()
    if override_file:
      message = textwrap.dedent("""
          The environment variable [{envvar}] is set to:
            [{override_file}]
          Credentials will still be generated to the default location:
            [{default_file}]
          To use these credentials, unset this environment variable before
          running your application.
          """.format(envvar=client.GOOGLE_APPLICATION_CREDENTIALS,
                     override_file=override_file,
                     default_file=auth_util.ADCFilePath()))
      console_io.PromptContinue(
          message=message, throw_if_unattended=True, cancel_on_no=True)

    scopes = args.scopes or [auth_util.CLOUD_PLATFORM_SCOPE]
    launch_browser = auth_util.ShouldLaunchBrowser(args.launch_browser)
    if args.client_id_file:
      creds = auth_util.DoInstalledAppBrowserFlow(
          launch_browser=launch_browser,
          scopes=scopes,
          client_id_file=args.client_id_file)
    else:
      creds = auth_util.DoInstalledAppBrowserFlow(
          launch_browser=launch_browser,
          scopes=scopes,
          client_id=auth_util.DEFAULT_CREDENTIALS_DEFAULT_CLIENT_ID,
          client_secret=auth_util.DEFAULT_CREDENTIALS_DEFAULT_CLIENT_SECRET)

    full_path = auth_util.SaveCredentialsAsADC(creds)
    log.status.Print('\nCredentials saved to file: [{f}]'.format(f=full_path))
    log.status.Print(
        '\n'
        'These credentials will be used by any library that requests\n'
        'Application Default Credentials.')
    return creds
