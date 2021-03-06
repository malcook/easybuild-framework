# #
# Copyright 2014-2015 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://vscentrum.be/nl/en),
# the Hercules foundation (http://www.herculesstichting.be/in_English)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# http://github.com/hpcugent/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
# #
"""
Unit tests for ModulesTool class.

@author: Stijn De Weirdt (Ghent University)
"""
import os
import re
import tempfile
from vsc.utils import fancylogger

from test.framework.utilities import EnhancedTestCase
from unittest import main as unittestmain
from unittest import TestLoader
from distutils.version import StrictVersion

import easybuild.tools.options as eboptions
from easybuild.tools import config, modules
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.config import build_option
from easybuild.tools.filetools import which
from easybuild.tools.modules import modules_tool, Lmod
from test.framework.utilities import init_config


class MockModulesTool(modules.ModulesTool):
    """ MockModule class"""
    COMMAND = 'echo'
    VERSION_OPTION = '1.0'
    VERSION_REGEXP = r'(?P<version>\d\S*)'
    # redirect to stderr, ignore 'echo python' ($0 and $1)
    COMMAND_SHELL = ["bash", "-c", "echo $2 $3 $4 1>&2"]


class BrokenMockModulesTool(MockModulesTool):
    """MockModulesTool class that is broken unless environment command is set"""
    COMMAND = '/does/not/exist'
    COMMAND_ENVIRONMENT = 'BMMT_CMD'


class ModulesToolTest(EnhancedTestCase):
    """ Testcase for ModulesTool """

    def setUp(self):
        """Testcase setup."""
        super(ModulesToolTest, self).setUp()

        # keep track of original 'module' function definition so we can restore it
        self.orig_module = os.environ.get('module', None)

    def test_mock(self):
        """Test the mock module"""
        os.environ['module'] = "() {  eval `/bin/echo $*`\n}"

        # ue empty mod_path list, otherwise the install_path is called
        mmt = MockModulesTool(mod_paths=[], testing=True)

        # the version of the MMT is the commandline option
        self.assertEqual(mmt.version, StrictVersion(MockModulesTool.VERSION_OPTION))

        cmd_abspath = which(MockModulesTool.COMMAND)

        # make sure absolute path of module command is being used
        self.assertEqual(mmt.cmd, cmd_abspath)

    def test_environment_command(self):
        """Test setting cmd via enviroment"""
        os.environ['module'] = "() { %s $*\n}" % BrokenMockModulesTool.COMMAND

        try:
            bmmt = BrokenMockModulesTool(mod_paths=[], testing=True)
            # should never get here
            self.assertTrue(False, 'BrokenMockModulesTool should fail')
        except EasyBuildError, err:
            self.assertTrue('command is not available' in str(err))

        os.environ[BrokenMockModulesTool.COMMAND_ENVIRONMENT] = MockModulesTool.COMMAND
        os.environ['module'] = "() { /bin/echo $*\n}"
        BrokenMockModulesTool._instances.pop(BrokenMockModulesTool, None)
        bmmt = BrokenMockModulesTool(mod_paths=[], testing=True)
        cmd_abspath = which(MockModulesTool.COMMAND)

        self.assertEqual(bmmt.version, StrictVersion(MockModulesTool.VERSION_OPTION))
        self.assertEqual(bmmt.cmd, cmd_abspath)

        # clean it up
        del os.environ[BrokenMockModulesTool.COMMAND_ENVIRONMENT]

    def test_module_mismatch(self):
        """Test whether mismatch detection between modules tool and 'module' function works."""
        # redefine 'module' function (deliberate mismatch with used module command in MockModulesTool)
        os.environ['module'] = "() {  eval `/tmp/Modules/$MODULE_VERSION/bin/modulecmd bash $*`\n}"
        error_regex = ".*pattern .* not found in defined 'module' function"
        self.assertErrorRegex(EasyBuildError, error_regex, MockModulesTool, testing=True)

        # check whether escaping error by allowing mismatch via build options works
        build_options = {
            'allow_modules_tool_mismatch': True,
        }
        init_config(build_options=build_options)

        fancylogger.logToFile(self.logfile)

        mt = MockModulesTool(testing=True)
        f = open(self.logfile, 'r')
        logtxt = f.read()
        f.close()
        warn_regex = re.compile("WARNING .*pattern .* not found in defined 'module' function")
        self.assertTrue(warn_regex.search(logtxt), "Found pattern '%s' in: %s" % (warn_regex.pattern, logtxt))

        # redefine 'module' function with correct module command
        os.environ['module'] = "() {  eval `/bin/echo $*`\n}"
        MockModulesTool._instances.pop(MockModulesTool)
        mt = MockModulesTool(testing=True)
        self.assertTrue(isinstance(mt.loaded_modules(), list))  # dummy usage

        # a warning should be logged if the 'module' function is undefined
        del os.environ['module']
        MockModulesTool._instances.pop(MockModulesTool)
        mt = MockModulesTool(testing=True)
        f = open(self.logfile, 'r')
        logtxt = f.read()
        f.close()
        warn_regex = re.compile("WARNING No 'module' function defined, can't check if it matches .*")
        self.assertTrue(warn_regex.search(logtxt), "Pattern %s found in %s" % (warn_regex.pattern, logtxt))

        fancylogger.logToFile(self.logfile, enable=False)

    def test_lmod_specific(self):
        """Lmod-specific test (skipped unless Lmod is used as modules tool)."""
        lmod_abspath = which(Lmod.COMMAND)
        # only run this test if 'lmod' is available in $PATH
        if lmod_abspath is not None:
            build_options = {
                'allow_modules_tool_mismatch': True,
                'update_modules_tool_cache': True,
            }
            init_config(build_options=build_options)

            # drop any location where 'lmod' or 'spider' can be found from $PATH
            paths = os.environ.get('PATH', '').split(os.pathsep)
            new_paths = []
            for path in paths:
                lmod_cand_path = os.path.join(path, Lmod.COMMAND)
                spider_cand_path = os.path.join(path, 'spider')
                if not os.path.isfile(lmod_cand_path) and not os.path.isfile(spider_cand_path):
                    new_paths.append(path)
            os.environ['PATH'] = os.pathsep.join(new_paths)

            # make sure $MODULEPATH contains path that provides some modules
            os.environ['MODULEPATH'] = os.path.abspath(os.path.join(os.path.dirname(__file__), 'modules'))

            # initialize Lmod modules tool, pass full path to 'lmod' via $LMOD_CMD
            os.environ['LMOD_CMD'] = lmod_abspath
            lmod = Lmod(testing=True)

            # obtain list of availabe modules, should be non-empty
            self.assertTrue(lmod.available(), "List of available modules obtained using Lmod is non-empty")

            # test updating local spider cache (but don't actually update the local cache file!)
            self.assertTrue(lmod.update(), "Updated local Lmod spider cache is non-empty")

    def tearDown(self):
        """Testcase cleanup."""
        super(ModulesToolTest, self).tearDown()

        # restore 'module' function
        if self.orig_module is not None:
            os.environ['module'] = self.orig_module
        else:
            if 'module' in os.environ:
                del os.environ['module']


def suite():
    """ returns all the testcases in this module """
    return TestLoader().loadTestsFromTestCase(ModulesToolTest)


if __name__ == '__main__':
    unittestmain()

