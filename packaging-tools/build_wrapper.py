#!/usr/bin/env python
#############################################################################
##
## Copyright (C) 2016 The Qt Company Ltd.
## Copyright (C) 2014 BlackBerry Limited. All rights reserved.
## Contact: https://www.qt.io/licensing/
##
## This file is part of the release tools of the Qt Toolkit.
##
## $QT_BEGIN_LICENSE:GPL-EXCEPT$
## Commercial License Usage
## Licensees holding valid commercial Qt licenses may use this file in
## accordance with the commercial license agreement provided with the
## Software or, alternatively, in accordance with the terms contained in
## a written agreement between you and The Qt Company. For licensing terms
## and conditions see https://www.qt.io/terms-conditions. For further
## information use the contact form at https://www.qt.io/contact-us.
##
## GNU General Public License Usage
## Alternatively, this file may be used under the terms of the GNU
## General Public License version 3 as published by the Free Software
## Foundation with exceptions as appearing in the file LICENSE.GPL3-EXCEPT
## included in the packaging of this file. Please review the following
## information to ensure the GNU General Public License requirements will
## be met: https://www.gnu.org/licenses/gpl-3.0.html.
##
## $QT_END_LICENSE$
##
#############################################################################

"""Scripts to generate SDK installer based on open source InstallerFramework"""

# import the print function which is used in python 3.x
from __future__ import print_function
import ConfigParser
import optionparser
import argparse
import collections
from glob import glob
import os
import sys
import re
import shutil
import platform
import urllib
import urlparse
from time import gmtime, strftime
import bld_ifw_tools
from bld_ifw_tools import IfwOptions
import bld_utils
import bldinstallercommon
import release_build_handler
import bld_icu_tools
import pkg_constants
from pkg_constants import ICU_BUILD_OUTPUT_DIR


# ----------------------------------------------------------------------
SCRIPT_ROOT_DIR             = os.path.dirname(os.path.realpath(__file__))
REPO_OUTPUT_DIR             = os.path.normpath(os.path.join(SCRIPT_ROOT_DIR, 'repository'))
PKG_NODE_ROOT               = os.getenv('PKG_NODE_ROOT', os.path.abspath(os.path.join(__file__, '../../../')))
WORK_DIR                    = os.path.join(PKG_NODE_ROOT, 'build')
OPTION_PARSER               = 0
QTCREATOR_VERSION           = ''
QTCREATOR_VERSION_DESCRIPTION = ''
BIN_TARGET_DIRS             = {} # dictionary populated based on the /packaging-tools/releases/build-meta
CI_TARGET_POSTFIX           = {} # dictionary populated based on the /packaging-tools/releases/build-meta
EXTRA_ENV                   = dict(os.environ)
BUILD_META_INFO_FILE        = 'releases/build-meta'




###########################################
# Environment variable combining
###########################################
def combine_env_variable(a, b):
    if platform.system().lower().startswith('win'):
        return a + ';' + b
    else:
        return a + ':' + b

def combine_environment_dicts(a, b):
    return dict(a.items() + b.items() +
                [(k, combine_env_variable(a[k], b[k])) for k in set(b) & set(a)])



###############################
# sign windows executable
###############################
def sign_windows_executable(file_path, working_dir, abort_on_fail):
    cmd_args = [r'C:\Utils\sign\signtool.exe', 'sign', '/v', '/du', optionDict['SIGNING_SERVER'], '/p', optionDict['SIGNING_PASSWORD'],
                '/t', 'http://timestamp.verisign.com/scripts/timestamp.dll', '/f', r'C:\utils\sign\keys.pfx', file_path]
    bldinstallercommon.do_execute_sub_process(cmd_args, working_dir, abort_on_fail)


###############################
# Unlock keychain
###############################
def unlock_keychain():
    cmd_args = ['/Users/qt/unlock-keychain.sh']
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)


###############################
# Lock keychain
###############################
def lock_keychain():
    cmd_args = ['/Users/qt/lock-keychain.sh']
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)


###############################
# sign windows executable
###############################
def sign_mac_executable(file_path, working_dir, abort_on_fail):
    unlock_keychain()
    s_arg = 'Developer ID Application: The Qt Company Oy'
    cmd_args = ['codesign', '--verbose=3', '-r', '/Users/qt/csreq_qt_company.txt', '-s', s_arg, file_path]
    bldinstallercommon.do_execute_sub_process(cmd_args, working_dir, abort_on_fail)


###############################
# handle_qt_licheck_build
###############################
def handle_qt_licheck_build(optionDict):
    if optionDict['LICENSE'] == 'enterprise':
        # Build license checker
        exe_dir = WORK_DIR + '/license-managing/licheck'
        upload_path = optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/licheck/'
        if optionDict['TARGET_ENV'].lower().startswith("win"):
            cmd_args = [r'c:\Utils\jom\jom.exe', '-f', 'Makefile_win']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            cmd_args = [optionDict['SCP_COMMAND'], 'licheck.exe', upload_path]
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
        elif optionDict['TARGET_ENV'].lower().startswith("linux"):
            cmd_args = ['make', '-j6', '-f', 'Makefile_unix']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            cmd_args = ['strip', 'licheck']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            if optionDict['TARGET_ENV'].find("x64") >= 1:
                cmd_args = ['rsync', '-r', 'licheck', upload_path +'licheck64']
            else:
                cmd_args = ['rsync', '-r', 'licheck', upload_path +'licheck32']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
        else:
            cmd_args = ['make', '-j6', '-f', 'Makefile_osx']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            cmd_args = ['strip', 'licheck']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            cmd_args = ['rsync', '-r', 'licheck', upload_path +'licheck_mac']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
    else:
        #opensource, do nothing
        print('*** opensource build, nothing to build ...')


def get_ifw_remote_destination_dir(optionDict):
    """Deternime the exact destination directory for ifw build artifacts."""
    ifw_branch = optionDict['IFW_GIT_VERSION'] # mandatory env variable
    # Destination dir name on network disk for build artifacts, optional.
    ifw_dest_dir_name = optionDict.get('IFW_REMOTE_RESULT_DEST_DIR_NAME', '')
    ifw_dest_dir_name = ifw_dest_dir_name if ifw_dest_dir_name else ifw_branch
    ifw_dest_dir = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/ifw/' + ifw_dest_dir_name
    return (ifw_dest_dir_name, ifw_dest_dir)


###############################
# handle_ifw_build()
###############################
def handle_ifw_build(optionDict):
    os.chdir(SCRIPT_ROOT_DIR)
    # Qt
    qt_src_pkg = optionDict['IFW_QT_SRC_PKG'] # mandatory env variable
    # OpenSSL
    openssl_dir = optionDict.get('IFW_OPENSSL_DIR', '')
    qt_configure_options = bld_ifw_tools.get_default_qt_configure_options(openssl_dir)
    # Installer-Framework
    ifw_url    = optionDict['IFW_GIT_URL'] # mandatory env variable
    ifw_branch = optionDict['IFW_GIT_VERSION'] # mandatory env variable
    (ifw_dest_dir_name, ifw_dest_dir) = get_ifw_remote_destination_dir(optionDict)

    ifw_qmake_args = IfwOptions.default_qt_installer_framework_qmake_args
    # check for debug build
    ifw_debug_build = optionDict.get('IFW_DEBUG_BUILD', '')
    if ifw_debug_build in ['yes', 'true', '1']:
        qt_configure_options = qt_configure_options.replace('-release', '-debug')
        ifw_qmake_args = ' '.join(ifw_qmake_args).replace('-config release', '-config debug').split()
    # extra qt configure options for qt/ifw build
    if 'IFW_QT_EXTRA_CONFIGURE_OPTIONS' in optionDict:
        qt_configure_options += ' ' + optionDict['IFW_QT_EXTRA_CONFIGURE_OPTIONS']
    # Product Key Checker
    product_key_checker_pri = ''
    if optionDict['LICENSE'] == 'enterprise':
        product_key_checker_pri = optionDict['PRODUCT_KEY_CHECKER_PRI']
        if product_key_checker_pri:
            temp = bldinstallercommon.locate_file(PKG_NODE_ROOT, product_key_checker_pri)
            product_key_checker_pri = temp if temp else product_key_checker_pri

    # options object for ifw build
    ifw_bld_options = IfwOptions(qt_src_pkg,
                                 qt_configure_options,
                                 ifw_url, ifw_branch,
                                 ifw_qmake_args,
                                 openssl_dir,
                                 product_key_checker_pri,
                                 False)
    # build ifw tools
    bld_ifw_installer = True if 'CREATE_IFW_INSTALLER' in optionDict else False
    bld_ifw_tools.build_ifw(ifw_bld_options, bld_ifw_installer)

    ## create destination dirs on network disk
    # internal
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], ifw_dest_dir)
    # public
    ifw_snapshot_export = optionDict.get('IFW_EXPORT_SNAPSHOT', '')
    if optionDict['LICENSE'] == 'opensource' and ifw_snapshot_export in ['yes', 'true', '1']:
        # public server address and path
        ext_server_base_url  = optionDict['EXT_SERVER_BASE_URL']
        ext_server_base_path = optionDict['EXT_SERVER_BASE_PATH']
        # public server directories
        ext_dest_dir = ext_server_base_path + '/snapshots/ifw/' + ifw_dest_dir_name + '/' + optionDict['BUILD_NUMBER']
        cmd_args_mkdir_pkg = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR']]
        cmd_args_mkdir_ext = cmd_args_mkdir_pkg + ['ssh', ext_server_base_url, 'mkdir', '-p', ext_dest_dir]
        bldinstallercommon.do_execute_sub_process(cmd_args_mkdir_ext, SCRIPT_ROOT_DIR)

    artifacts_dir = os.path.join(SCRIPT_ROOT_DIR, pkg_constants.IFW_BUILD_ARTIFACTS_DIR)
    # Create disk image(s) if any .app found
    if bldinstallercommon.is_mac_platform():
        artifact_list = [f for f in os.listdir(artifacts_dir) if f.endswith(".app")]
        for item in artifact_list:
            base_name = item.split(".app")[0]
            # create disk image
            cmd_args = ['hdiutil', 'create', '-srcfolder', os.path.join(artifacts_dir, item),
                        '-volname', base_name, '-format', 'UDBZ',
                        os.path.join(artifacts_dir, base_name + '.dmg'),
                        '-ov', '-scrub', '-size', '1g']
            bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)
    # Sign if there is anything to be signed
    file_list = [f for f in os.listdir(artifacts_dir) if f.endswith((".dmg", ".run", ".exe"))]
    for file_name in file_list:
        installer_name, installer_name_base, installer_name_final = generate_installer_final_name(optionDict, file_name)
        sign_installer(artifacts_dir, installer_name, installer_name_base)
    # Upload
    files_to_upload = [f for f in os.listdir(artifacts_dir) if f.endswith((".dmg", ".run", ".exe", ".7z"))]
    for item in files_to_upload:
        if bldinstallercommon.is_win_platform():
            cmd_args = [optionDict['SCP_COMMAND'], item, optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/ifw/' + ifw_dest_dir_name + '/']
        else:
            cmd_args = ['rsync', '-r', './', optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/ifw/' + ifw_dest_dir_name + '/']
        bldinstallercommon.do_execute_sub_process(cmd_args, artifacts_dir)
    # Copy ifw .7z files to public server
    if optionDict['LICENSE'] == 'opensource' and ifw_snapshot_export in ['yes', 'true', '1']:
        cmd_args_copy_ifw_pkg = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR']]
        cmd_args_copy_ifw_ext = cmd_args_copy_ifw_pkg + ['scp', ifw_dest_dir + '/' + 'installer-framework-build*.7z', ext_server_base_url + ':' + ext_dest_dir]
        bldinstallercommon.do_execute_sub_process(cmd_args_copy_ifw_ext, SCRIPT_ROOT_DIR)


###############################
# Remote copy files
###############################
def remote_copy_archives(remote_target, from_where_path):
    remote_copy_cmd = optionDict['SCP_COMMAND']
    if bldinstallercommon.is_mac_platform():
        remote_copy_cmd = 'rsync'
    dir_list = os.listdir(from_where_path)
    for file_name in dir_list:
        if file_name.endswith(".7z"):
            cmd_args = [remote_copy_cmd, file_name, remote_target]
            bldinstallercommon.do_execute_sub_process(cmd_args, from_where_path)


###############################
# Generate binary target dictionary
###############################
def generate_bin_target_dictionary():
    global BIN_TARGET_DIRS
    global CI_TARGET_POSTFIX
    # parse module exclude list from release description file
    conf_file_base_path = os.path.join(SCRIPT_ROOT_DIR, BUILD_META_INFO_FILE)
    if not os.path.isfile(conf_file_base_path):
        raise IOError('*** Not a valid release description file: %s' % conf_file_base_path)
    parser = ConfigParser.ConfigParser()
    parser.readfp(open(conf_file_base_path))
    # parse
    for s in parser.sections():
        section_parts = s.split('.')
        if section_parts[0] != 'meta':
            continue
        build_target_dir = bldinstallercommon.safe_config_key_fetch(parser, s, 'build_target_dir')
        ci_target_postfix = bldinstallercommon.safe_config_key_fetch(parser, s, 'ci_target_postfix')
        build_node_labels = bldinstallercommon.safe_config_key_fetch(parser, s, 'build_node_labels').replace(' ', '')
        label_list = build_node_labels.split(',')
        if not build_target_dir:
            raise RuntimeError('*** No build target directory defined for: %s' % s)
        if not label_list:
            raise RuntimeError('*** No build slave label defined for: %s' % s)
        for label in label_list:
            BIN_TARGET_DIRS[label] = build_target_dir
            if ci_target_postfix:
                CI_TARGET_POSTFIX[label] = ci_target_postfix


###############################
# handle_gammaray_build
###############################
def handle_gammaray_build(optionDict):
    def cloneGammarayRepo(gitUrl, gitBranchOrTag, checkoutDir):
        srcPath = os.path.join(WORK_DIR, checkoutDir)
        if os.path.exists(srcPath):
            shutil.rmtree(srcPath)
        os.makedirs(srcPath)
        bldinstallercommon.clone_repository(gitUrl, gitBranchOrTag, srcPath)

    # Get gammaray sources if not present yet
    if 'GAMMARAY_GIT_URL' in optionDict:
        cloneGammarayRepo(optionDict['GAMMARAY_GIT_URL'], optionDict['GAMMARAY_GIT_BRANCH_OR_TAG'], optionDict['GAMMARAY_CHECKOUT_DIR_NAME'])
    if 'KDSME_GIT_URL' in optionDict:
        cloneGammarayRepo(optionDict['KDSME_GIT_URL'], optionDict['KDSME_GIT_BRANCH_OR_TAG'], optionDict['KDSME_CHECKOUT_DIR_NAME'])

    gammaray_version = optionDict['GAMMARAY_VERSION']
    graphviz_filename = optionDict['GRAPHVIZ_BASE_FILENAME'] + '-' + optionDict['TARGET_ENV'] + '.7z'
    graphviz_url = (optionDict['PACKAGE_STORAGE_SERVER_PATH_HTTP'] + '/' + optionDict['GRAPHVIZ_BASE_PATH']
                    + '/' + graphviz_filename)
    graphviz_download_filepath = os.path.join(WORK_DIR, graphviz_filename)
    graphviz_target_path = os.path.join(WORK_DIR, 'graphviz')

    icu_libs = optionDict.get('ICU_LIBS') # optional
    qt_base_path = optionDict['QT_BASE_PATH']
    qt_module_urls = []

    qt_modules = ['qtbase', 'qtdeclarative', 'qtscript', 'qtxmlpatterns']
    pkg_base_path = optionDict['PACKAGE_STORAGE_SERVER_PATH_HTTP']
    # Check if the archives reside on network disk (http) or on local file system
    scheme = "" if urlparse.urlparse(pkg_base_path).scheme != "" else "file://"
    qt_base_url = scheme + pkg_base_path + '/' + qt_base_path
    qt_postfix = CI_TARGET_POSTFIX[optionDict['TARGET_ENV']]
    qt_module_urls = [qt_base_url + '/' + module + '/' + module + '-' + qt_postfix + '.7z'
                      for module in qt_modules]

    # download and extract graphviz
    if os.path.isdir(graphviz_target_path):
        shutil.rmtree(graphviz_target_path)
    if os.path.isfile(graphviz_download_filepath):
        os.remove(graphviz_download_filepath)
    bld_utils.download(graphviz_url, graphviz_download_filepath)
    extract_graphviz_cmd = ['7z', 'x', '-y', graphviz_download_filepath,
                   '-o' + graphviz_target_path]
    bldinstallercommon.do_execute_sub_process(extract_graphviz_cmd, WORK_DIR)

    build_environment = dict(os.environ)
    if bldinstallercommon.is_linux_platform():
        build_environment['LD_LIBRARY_PATH'] = ':'.join(filter(None, [os.path.join(graphviz_target_path, 'lib'),
                                                                      build_environment.get('LD_LIBRARY_PATH')]))

    def common_gammaray_args():
        cmd_args = ['python', '-u', os.path.join(SCRIPT_ROOT_DIR, 'bld_module.py'),
                    '--clean',
                    '--use-cmake']
        for module_url in qt_module_urls:
            cmd_args.extend(['--qt5_module_url', module_url])
        if bldinstallercommon.is_linux_platform():
            cmd_args.extend(['--icu7z', icu_libs])
        if bldinstallercommon.is_win_platform():
            cmd_args.extend(['--add-config-arg=-G', '--add-config-arg=NMake Makefiles',
                             '--buildcommand', 'nmake',
                             '--installcommand', 'nmake'])
        return cmd_args

    # build kdstatemachineeditor
    cmd_args = common_gammaray_args()
    cmd_args.extend(['--module_dir', os.path.join(WORK_DIR, 'kdsme'),
                     '--module-name', 'kdsme',
                     '--qt5path', os.path.join(WORK_DIR, 'kdsme_qt5_install'),
                     '--add-config-arg=-DBUILD_EXAMPLES=OFF',
                     '--add-config-arg=-DBUILD_TESTING=OFF',
                     '--add-config-arg=-DGRAPHVIZ_ROOT={0}'.format(graphviz_target_path)])
    bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR, extra_env=build_environment)

    # build gammaray
    cmd_args = common_gammaray_args()
    cmd_args.extend(['--module_dir', os.path.join(WORK_DIR, 'gammaray'),
                     '--module-name', 'gammaray',
                     '--qt5path', os.path.join(WORK_DIR, 'gammaray_qt5_install'),
                     '--add-config-arg=-DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE',
                     '--qt5_module_url', bld_utils.file_url(os.path.join(SCRIPT_ROOT_DIR, 'module_archives', 'qt5_kdsme.7z'))])
    bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR, extra_env=build_environment)

    # upload
    base_path = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/gammaray/' + gammaray_version
    base_upload_path = base_path + '/' + optionDict['BUILD_NUMBER']
    upload_path =  base_upload_path + '/' + BIN_TARGET_DIRS[optionDict['TARGET_ENV']]
    latest_path = base_path + '/latest'
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], upload_path)
    update_latest_link(optionDict, base_upload_path, latest_path)
    for module in ['kdsme', 'gammaray']:
        cmd_args = [optionDict['SCP_COMMAND'], 'qt5_{0}.7z'.format(module),
                    optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + upload_path + '/']
        bldinstallercommon.do_execute_sub_process(cmd_args, os.path.join(SCRIPT_ROOT_DIR, 'module_archives'))


PluginConf = collections.namedtuple('PluginConf', ['git_url', 'branch_or_tag', 'checkout_dir'])


def parseQtCreatorPlugins(pkgConfFile):
    """Parse available Qt Creator plugins from configuration file"""
    pluginList = []
    if not pkgConfFile:
        return pluginList
    pluginOptions = optionparser.getPkgOptions(pkgConfFile)
    sectionName = "QtCreator.Build.Plugin"
    keyName = "plugins"
    if not pluginOptions.optionExists(sectionName, keyName):
        return pluginList
    pluginConfList = pluginOptions.configSectionMap(sectionName)[keyName]
    for pluginName in pluginConfList.replace(" ", "").replace("\n", "").split(","):
        section = "QtCreator.Build.Plugin." + pluginName
        pluginUrl = pluginOptions.configSectionMap(section)["QTC_PLUGIN_GIT_URL"]
        branchOrTag = pluginOptions.configSectionMap(section)["QTC_PLUGIN_GIT_BRANCH_OR_TAG"]
        checkoutDirName = pluginOptions.configSectionMap(section)["QTC_PLUGIN_CHECKOUT_DIR_NAME"]
        plugin = PluginConf(git_url=pluginUrl, branch_or_tag=branchOrTag, checkout_dir=checkoutDirName)
        pluginList.extend([plugin])
    return pluginList


###############################
# handle_qt_creator_build
###############################
def handle_qt_creator_build(optionDict, qtCreatorPlugins):
    def get_optimized_libclang_dll(optionDict, pkg_base_path):
        url = (pkg_base_path + '/' + optionDict['CLANG_FILEBASE'] + '-windows-mingw.7z')
        extraction_path = os.path.join(WORK_DIR, 'qt-creator_temp_optimizedlibclang')
        download_filepath = os.path.join(extraction_path, 'libclang.7z')
        bld_utils.download(url, download_filepath)
        bldinstallercommon.extract_file(download_filepath, extraction_path)
        return os.path.join(extraction_path, 'libclang', 'bin', 'libclang.dll')

    target_env_dir = BIN_TARGET_DIRS[optionDict['TARGET_ENV']]
    build_environment = dict(os.environ)

    # Get Qt Creator sources if not present yet
    if 'QT_CREATOR_GIT_URL' in optionDict:
        qtCreatorSourceDirectory = os.path.join(WORK_DIR, 'qt-creator')
        if os.path.exists(qtCreatorSourceDirectory):
            shutil.rmtree(qtCreatorSourceDirectory)
        os.makedirs(qtCreatorSourceDirectory)
        bldinstallercommon.clone_repository(optionDict['QT_CREATOR_GIT_URL'], optionDict['QT_CREATOR_GIT_BRANCH'],
                                            qtCreatorSourceDirectory, full_clone=True, init_subrepos=True)
    # Get Qt Creator plugin sources if not present yet
    for pluginConf in qtCreatorPlugins:
        checkoutDir = os.path.join(WORK_DIR, pluginConf.checkout_dir)
        if pluginConf.git_url:
            if os.path.exists(checkoutDir):
                shutil.rmtree(checkoutDir)
            os.makedirs(checkoutDir)
            bldinstallercommon.clone_repository(pluginConf.git_url, pluginConf.branch_or_tag, checkoutDir, full_clone=True)

    # Build time variables
    pkg_base_path = optionDict['PACKAGE_STORAGE_SERVER_PATH_HTTP']
    # Check if the archives reside on network disk (http) or on local file system
    scheme = "" if urlparse.urlparse(pkg_base_path).scheme != "" else "file://"
    pkg_base_path = scheme + pkg_base_path
    pkg_storage_server = optionDict['PACKAGE_STORAGE_SERVER_ADDR']
    base_path = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/qtcreator/snapshots/' + optionDict['QT_CREATOR_VERSION']
    snapshot_server = optionDict.get('SNAPSHOT_SERVER') # optional
    snapshot_path = optionDict['SNAPSHOT_SERVER_PATH'] # optional
    qt_base_path = optionDict['QTC_QT_BASE_DIR']
    qtcreator_version = optionDict['QT_CREATOR_VERSION']
    qtcreator_edition_name = optionDict['QT_CREATOR_EDITION_NAME']
    qtcreator_version_description = optionDict['QT_CREATOR_VERSION_DESCRIPTION']
    installer_patch = optionDict.get('INSTALLER_PATCH') # optional
    build_id = optionDict['BUILD_NUMBER']
    icu_libs = optionDict.get('ICU_LIBS') # optional
    openssl_libs = optionDict.get('OPENSSL_LIBS') # optional

    # Define paths for pre-built kdsme, graphviz and gammaray packages
    kdsme_url = (pkg_base_path + '/' + optionDict["GAMMARAY_BASE_DIR"] + '/' + target_env_dir + '/qt5_kdsme.7z')
    graphviz_url = (pkg_base_path + '/' + optionDict["GRAPHVIZ_BASE_DIR"] + '-' + optionDict['TARGET_ENV'] + '.7z')
    gammaray_url = (pkg_base_path + '/' + optionDict["GAMMARAY_BASE_DIR"] + '/' + target_env_dir + '/qt5_gammaray.7z')

    # Install clang package
    clang_url = (pkg_base_path + '/'
                    + optionDict['CLANG_FILEBASE'] + '-'
                    + optionDict['QTC_PLATFORM'] + '.7z')
    clang_download_filepath = os.path.join(WORK_DIR, 'qt-creator_temp')
    for item in [clang_download_filepath, os.path.join(WORK_DIR, 'libclang')]:
        if os.path.exists(item):
            shutil.rmtree(item)
    os.makedirs(clang_download_filepath)
    clang_download_filepath = os.path.join(clang_download_filepath, 'libclang.7z')
    clang_target_path = WORK_DIR
    bld_utils.download(clang_url, clang_download_filepath)
    bldinstallercommon.extract_file(clang_download_filepath, clang_target_path)
    build_environment['LLVM_INSTALL_DIR'] = os.path.join(WORK_DIR, 'libclang')
    if bldinstallercommon.is_win_platform() and not '4.1' in qtcreator_version:
        optimized_libclang_dll = get_optimized_libclang_dll(optionDict, pkg_base_path)
        target_libclang_dll = os.path.join(build_environment['LLVM_INSTALL_DIR'], 'bin', 'libclang.dll')
        shutil.copyfile(optimized_libclang_dll, target_libclang_dll)

    # Qt Creator build depends on pre-built Qt binary packages.
    # Define the exact archive locations for each required module.
    qt_modules = ['qtbase', 'qtdeclarative', 'qtgraphicaleffects',
                  'qtimageformats', 'qtlocation', 'qtmacextras',
                  'qtquickcontrols', 'qtquickcontrols2', 'qtscript', 'qtsvg', 'qttools',
                  'qttranslations', 'qtx11extras', 'qtxmlpatterns']
    qt_postfix = CI_TARGET_POSTFIX[optionDict['TARGET_ENV']]
    qt_module_filenames = [module + '-' + qt_postfix + '.7z' for module in qt_modules]
    qt_module_urls = [pkg_base_path + '/' + qt_base_path + '/' + module + '/' + filename
                      for (module, filename) in zip(qt_modules, qt_module_filenames)]

    # Define Qt Creator build script arguments
    common_arguments = []
    if not bldinstallercommon.is_win_platform():
        common_arguments.extend(['--installcommand', 'make -j1'])
    else:
        common_arguments.extend(['--buildcommand', os.path.normpath('C:/Utils/jom/jom.exe'),
                         '--installcommand', os.path.normpath('nmake.exe'),
                         '--sevenzippath', os.path.normpath('C:/Utils/sevenzip'),
                         '--gitpath', os.path.normpath('C:/Program Files/Git/bin')])
    cmd_args = ['python', '-u', os.path.normpath(SCRIPT_ROOT_DIR + '/bld_qtcreator.py'),
                '--clean',
                '--qt5path', os.path.normpath(WORK_DIR + '/qt5_install_dir'),
                '--versiondescription', '"' + qtcreator_version_description + '"']
    for module_url in qt_module_urls:
        cmd_args.extend(['--qt-module', module_url])

    if bldinstallercommon.is_linux_platform() and icu_libs:
        cmd_args.extend(['--icu7z', icu_libs])
    elif bldinstallercommon.is_mac_platform():
        cmd_args.extend(['--keychain_unlock_script', '/Users/qt/unlock-keychain.sh'])
    else:
        d3d_url = 'http://download.qt.io/development_releases/prebuilt/d3dcompiler/msvc2013/d3dcompiler_47-x86.7z'
        opengl_url = 'http://download.qt.io/development_releases/prebuilt/llvmpipe/windows/opengl32sw-32.7z'
        cmd_args.extend(['--d3dcompiler7z', d3d_url,
                         '--opengl32sw7z', opengl_url])
        if openssl_libs:
            cmd_args.extend(['--openssl7z', openssl_libs])
    cmd_args.extend(common_arguments)
    bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR, extra_env=build_environment)

    if bldinstallercommon.is_mac_platform():
        lock_keychain()

    # Qt Creator plugins
    plugin_dependencies = []
    additional_qmake_arguments = []
    Plugin = collections.namedtuple('Plugin', ['name', 'path', 'dependencies', 'modules', 'additional_arguments', 'include_in_package'])
    Plugin.__new__.__defaults__ = ([], [], [], True) # 'name' and 'path' are mandatory
    additional_plugins = []

    if os.path.isdir(os.path.join(WORK_DIR, "licensechecker")):
        additional_plugins.extend([Plugin(name='licensechecker', path='licensechecker')])
        additional_qmake_arguments = ['CONFIG+=licensechecker']
        plugin_dependencies = ['licensechecker']
    additional_plugins.extend([Plugin(name='vxworks-qtcreator-plugin', path='vxworks-qtcreator-plugin', dependencies=plugin_dependencies)])
    additional_plugins.extend([Plugin(name='isoiconbrowser', path='qtquickdesigner', dependencies=plugin_dependencies)])
    if bldinstallercommon.is_linux_platform():
        additional_plugins.extend([Plugin(name='perfparser', path='perfparser')])
        additional_qmake_arguments.extend(['PERFPARSER_BUNDLED_ELFUTILS=true',
                                           'PERFPARSER_APP_DESTDIR=' + os.path.join(WORK_DIR, 'perfparser-target', 'libexec', 'qtcreator'),
                                           'PERFPARSER_ELFUTILS_DESTDIR=' + os.path.join(WORK_DIR, 'perfparser-target', 'lib', 'qtcreator'),
                                           'PERFPARSER_APP_INSTALLDIR=' + os.path.join(WORK_DIR, 'perfparser-target', 'libexec', 'qtcreator'),
                                           'PERFPARSER_ELFUTILS_INSTALLDIR=' + os.path.join(WORK_DIR, 'perfparser-target', 'lib', 'qtcreator')
                                           ])
    if not bldinstallercommon.is_mac_platform():
        # download and extract
        #for item in [gammaray_url, kdsme_url, graphviz_url]:
        #    if not os.path.isdir(item):
        #        raise RuntimeError("Package url is invalid: {0}".format(gammaray_url))
        graphviz_download_filepath = os.path.join(WORK_DIR, 'qt-creator_temp', 'graphviz.7z')
        graphviz_target_path = os.path.join(WORK_DIR, 'graphviz')
        print("graphviz_download_filepath: {0}".format(graphviz_download_filepath))
        print("graphviz_target_path: {0}".format(graphviz_target_path))
        print("graphviz_url: {0}".format(graphviz_url))
        if os.path.isdir(graphviz_target_path):
            shutil.rmtree(graphviz_target_path)
        if os.path.isfile(graphviz_download_filepath):
            os.remove(graphviz_download_filepath)
        bld_utils.download(graphviz_url, graphviz_download_filepath)
        bldinstallercommon.extract_file(graphviz_download_filepath, graphviz_target_path)
        additional_plugins.extend([Plugin(name='perfprofiler', path='perfprofiler', dependencies=plugin_dependencies)])
        additional_plugins.extend([Plugin(name='b2qt-qtcreator-plugin', path='b2qt-qtcreator-plugin',
                                          dependencies=plugin_dependencies + ['perfprofiler'])])
        additional_plugins.extend([Plugin(name='gammarayintegration', path='gammarayintegration',
                                          dependencies=plugin_dependencies + ['b2qt-qtcreator-plugin', 'perfprofiler'],
                                          modules=[kdsme_url, gammaray_url],
                                          additional_arguments=[
                                          '--deploy-command', 'python',
                                          '--deploy-command=-u',
                                          '--deploy-command', os.path.join(WORK_DIR, 'gammarayintegration', 'scripts', 'deploy.py'),
                                          '--deploy-command=--graphviz-libs',
                                          '--deploy-command', graphviz_target_path],
                                          include_in_package=False)
                                  ])

    # Build Qt Creator plugins
    for plugin in additional_plugins:
        if not os.path.isdir(os.path.join(WORK_DIR, plugin.path)):
            continue

        cmd_arguments = ['python', '-u', os.path.join(SCRIPT_ROOT_DIR, 'bld_qtcreator_plugins.py'),
                         '--clean',
                         '--qtc-build', os.path.join(WORK_DIR, 'qt-creator_build'),
                         '--qtc-dev', os.path.join(WORK_DIR, 'qt-creator'),
                         '--plugin-path', os.path.join(WORK_DIR, plugin.path),
                         '--build-path', WORK_DIR]
        for module_filename in qt_module_filenames:
            cmd_arguments.extend(['--qt-module', bld_utils.file_url(os.path.join(WORK_DIR, 'qt-creator_temp', module_filename))])
        for module in plugin.modules:
            cmd_arguments.extend(['--qt-module', module])
        cmd_arguments.extend(plugin.additional_arguments)
        for qmake_arg in additional_qmake_arguments:
            cmd_arguments.extend(['--add-qmake-argument', qmake_arg])

        if bldinstallercommon.is_linux_platform():
            cmd_arguments.extend(['--icu7z', bld_utils.file_url(os.path.join(WORK_DIR, 'qt-creator_temp', os.path.basename(icu_libs)))])
        if bldinstallercommon.is_win_platform():
            cmd_arguments.extend(['--d3dcompiler7z', bld_utils.file_url(os.path.join(WORK_DIR, 'qt-creator_temp', os.path.basename(d3d_url))),
                                  '--opengl32sw7z', bld_utils.file_url(os.path.join(WORK_DIR, 'qt-creator_temp', os.path.basename(opengl_url)))])
            if openssl_libs:
                cmd_args.extend(['--openssl7z', bld_utils.file_url(os.path.join(WORK_DIR, 'qt-creator_temp', os.path.basename(openssl_libs)))])
        libs_paths = []
        for dependency_name in plugin.dependencies:
            matches = [dep for dep in additional_plugins if dep.name == dependency_name]
            if not matches:
                raise RuntimeError('did not find dependency "{0}" for plugin "{1}"'.format(dependency_name, plugin.name))
            dependency = matches[0]
            cmd_arguments.extend(['--plugin-search-path', os.path.join(WORK_DIR, dependency.path, 'plugins')])
            libs_base = os.path.join(WORK_DIR, dependency.name + '-target')
            if bldinstallercommon.is_mac_platform():
                libs_paths.append(os.path.join(libs_base, 'PlugIns'))
            else:
                libs_paths.append(os.path.join(libs_base, 'lib', 'qtcreator', 'plugins'))
        if libs_paths:
            cmd_arguments.extend(['--add-qmake-argument', 'LIBS*=' + ' '.join(['-L'+path for path in libs_paths])])
        cmd_arguments.extend(common_arguments)
        cmd_arguments.append(os.path.join(WORK_DIR, plugin.name + '.7z'))
        bldinstallercommon.do_execute_sub_process(cmd_arguments, WORK_DIR, extra_env=build_environment)
        # source package
        if bldinstallercommon.is_linux_platform():
            bldinstallercommon.do_execute_sub_process(['python', '-u', os.path.join(WORK_DIR, 'qt-creator', 'scripts', 'createSourcePackages.py'),
                                                       '-p', os.path.join(WORK_DIR, plugin.path),
                                                       '-n', plugin.name,
                                                       qtcreator_version, 'enterprise'], WORK_DIR)


    # Create opensource source package
    if bldinstallercommon.is_linux_platform():
        bldinstallercommon.do_execute_sub_process([os.path.join(WORK_DIR, 'qt-creator', 'scripts', 'createSourcePackages.py'), qtcreator_version, 'opensource'], WORK_DIR)

    # Create enterprise source package
    if installer_patch:
        bldinstallercommon.do_execute_sub_process(['git', 'am', '-3', installer_patch],
                                                  os.path.join(WORK_DIR, 'qt-creator'))
    if bldinstallercommon.is_linux_platform():
        bldinstallercommon.do_execute_sub_process([os.path.join(WORK_DIR, 'qt-creator', 'scripts', 'createSourcePackages.py'), qtcreator_version, 'enterprise'], WORK_DIR)

    # Upload
    # Qt Creator directory
    if qtcreator_edition_name:
        base_path += '_' + qtcreator_edition_name
    dir_path = base_path + '/' + build_id
    latest_path = base_path + '/latest'
    create_remote_dirs(optionDict, pkg_storage_server, dir_path + '/' + target_env_dir)
    update_latest_link(optionDict, dir_path, latest_path)

    # snapshot directory

    if snapshot_server and snapshot_path:
        if qtcreator_version:
            snapshot_path += '/' + qtcreator_version
        cmd_args = [optionDict['SSH_COMMAND'], pkg_storage_server, "ssh", snapshot_server,
            'mkdir', '-p', snapshot_path + '/' + build_id + '/installer_source/' + target_env_dir]
        bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR, True)
        cmd_args = [optionDict['SSH_COMMAND'], pkg_storage_server, "ssh", snapshot_server,
            'ln', '-sfn', snapshot_path + '/' + build_id, snapshot_path + '/latest']
        bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR, True)
        snapshot_path += '/' + build_id

    file_upload_list = [] # pairs (source, dest), source relative to WORK_DIR, dest relative to server + dir_path
    snapshot_upload_list = [] # pairs (source, dest), source relative to server + dir_path, dest relative to snapshot server + snapshot_path

    # macOS opensource dmg
    if bldinstallercommon.is_mac_platform():
        file_upload_list.append(('qt-creator_build/qt-creator.dmg', 'qt-creator-opensource-mac-x86_64-' + qtcreator_version + '.dmg'))

    # source packages
    if bldinstallercommon.is_linux_platform():
        source_package_list = glob(os.path.join(WORK_DIR, 'qt-creator-*-src-' + qtcreator_version + '.*'))
        file_upload_list.extend([(fn, '') for fn in source_package_list])

    # installer 7z sources
    file_upload_list.append(('qt-creator_build/qtcreator.7z', target_env_dir + '/qtcreator.7z'))
    file_upload_list.append(('qt-creator_build/qtcreator_dev.7z', target_env_dir + '/qtcreator_dev.7z'))
    snapshot_upload_list.append((target_env_dir + '/qtcreator.7z', 'installer_source/' + target_env_dir + '/qtcreator.7z'))
    snapshot_upload_list.append((target_env_dir + '/qtcreator_dev.7z', 'installer_source/' + target_env_dir + '/qtcreator_dev.7z'))
    for plugin in additional_plugins:
        plugin_name = plugin.name + '.7z'
        if os.path.isfile(os.path.join(WORK_DIR, plugin_name)):
            file_upload_list.append((plugin_name, target_env_dir + '/' + plugin_name))

    # upload files
    for source, destination in file_upload_list:
        cmd_args = [optionDict['SCP_COMMAND'], source, pkg_storage_server + ':' + dir_path + '/' + destination]
        bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR)
    if snapshot_server and snapshot_path:
        for source, destination in snapshot_upload_list:
            cmd_args = [optionDict['SSH_COMMAND'], pkg_storage_server, "scp",
                dir_path + '/' + source,
                snapshot_server + ':' + snapshot_path + '/' + destination]
            bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR)


###############################
# handle_offline_installer_build
###############################
def handle_offline_installer_build(optionDict):
    # Project name, default is always "qt"
    project_name = optionDict['PROJECT_NAME'] if 'PROJECT_NAME' in optionDict else 'qt'
    handle_installer_build(optionDict, project_name, 'offline')


###############################
# handle_online_installer_build
###############################
def handle_online_installer_build(optionDict):
    # Project name, default is always "qt"
    project_name = optionDict['PROJECT_NAME'] if 'PROJECT_NAME' in optionDict else 'online_installers'
    handle_installer_build(optionDict, project_name, 'online')


###############################
# handle_installer_build
###############################
def handle_installer_build(optionDict, project_name, installer_type):
    # Create remote directories under <project_name>/<version>
    remote_path_base                        = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + project_name + '/' + optionDict['VERSION'] + '/' + 'installers'  # TODO
    remote_path_snapshot                    = remote_path_base + '/' + optionDict['BUILD_NUMBER']
    remote_path_latest_available            = remote_path_base + '/' + 'latest_available'
    # ensure remote directories exist
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_path_snapshot)
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_path_latest_available)
    # Determine local installer output directory
    installer_output_dir = os.path.join(SCRIPT_ROOT_DIR, pkg_constants.INSTALLER_OUTPUT_DIR_NAME)
    # Create all installers for this host
    arch = 'x64' if (optionDict['TARGET_ENV'].find('64') != -1) else 'x86'
    release_build_handler.handle_installer_build(optionDict, installer_type, 'release', arch)
    # Generate installer file name list
    installer_list = []
    dir_list = [f for f in os.listdir(installer_output_dir) if not f.endswith(".app")]
    for file_name in dir_list:
        installer_name, installer_name_base, installer_name_final = generate_installer_final_name(optionDict, file_name)
        installer_list.append((file_name, installer_name, installer_name_base, installer_name_final))
    # Sign and copy to network drive
    for item in installer_list:
        installer_name = item[1]
        installer_name_base = item[2]
        installer_name_final = item[3]
        # sign
        sign_installer(installer_output_dir, installer_name, installer_name_base)
        # copy installer(s) to various locations:
        remote_copy_installer(optionDict, remote_path_snapshot, installer_name, installer_output_dir, installer_name_final)
        remote_copy_installer(optionDict, remote_path_latest_available, installer_name, installer_output_dir, installer_name_final)
        # Keep only the latest one in the "latest_available" directory i.e. delete the previous one
        replace_latest_successful_installer(optionDict, installer_name, installer_name_final, remote_path_latest_available, installer_output_dir)
    # Trigger rta cases
    trigger_rta(optionDict, os.path.join(SCRIPT_ROOT_DIR, pkg_constants.RTA_DESCRIPTION_FILE_DIR_NAME))
    # Do we upload the installers to opensource/public network drive?
    if 'EXPORT_OPENSOURCE_INSTALLER' in optionDict:
        # opensource distribution server address and path
        ext_server_base_url  = optionDict['EXT_SERVER_BASE_URL']
        ext_server_base_path = optionDict['EXT_SERVER_BASE_PATH']
        # opensource distribution server directories  #TODO, below
        ext_dest_dir = ext_server_base_path + '/snapshots/' + project_name + '/' + optionDict['VERSION'][:3] + '/' + optionDict['VERSION_FULL'] + '/' + optionDict['BUILD_NUMBER']
        cmd_args_mkdir_pkg = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR']]
        cmd_args_mkdir_ext = cmd_args_mkdir_pkg + ['ssh', ext_server_base_url, 'mkdir -p', ext_dest_dir]
        bldinstallercommon.do_execute_sub_process(cmd_args_mkdir_ext, SCRIPT_ROOT_DIR)
        # Copy installers
        for item in installer_list:
            remote_copy_installer_opensource(optionDict, remote_path_snapshot, ext_server_base_url, ext_dest_dir, item[3])


###############################
# replace_latest_successful_installer
###############################
def replace_latest_successful_installer(optionDict, installer_name, installer_name_final, ls_installer_dir, installer_output):
    # check installer type
    if 'online' in installer_name_final.lower():
        regex = re.compile('.*online')
        if "embedded" in installer_name_final.lower():
            regex = re.compile(r'.*online(?:(?!_\d{4}).)*')
    else:
        regex = re.compile('.*' + optionDict['VERSION_FULL'])
    installer_base_name = "".join(regex.findall(installer_name_final))
    if not installer_base_name:
        print('*** Empty installer base name string')
    else:
        old_installer = ls_installer_dir + '/' + installer_base_name + '*'
        # delete old installer
        cmd_args = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR'], 'rm', '-f', old_installer]
        bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR, False)
        # save new installer to latest_successful directory
        cmd_args = [optionDict['SCP_COMMAND'], installer_name, optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + ls_installer_dir + '/' + installer_name_final]
        bldinstallercommon.do_execute_sub_process(cmd_args, installer_output)


###############################
# Sign installer
###############################
def sign_installer(installer_output_dir, installer_name, installer_name_base):
    if installer_name.endswith(".dmg"):
        sign_mac_executable(installer_name_base + '.app', installer_output_dir, True)
        disk_image_filepath = os.path.join(installer_output_dir, installer_name_base) + '.dmg'
        cmd_args = ['hdiutil', 'create', '-srcfolder', os.path.join(installer_output_dir, installer_name_base) + '.app', '-volname', installer_name_base, '-format', 'UDBZ', disk_image_filepath, '-ov', '-scrub', '-size', '4g']
        bldinstallercommon.do_execute_sub_process(cmd_args, installer_output_dir)
        sign_mac_executable(disk_image_filepath, installer_output_dir, True)
    if installer_name.endswith(".exe"):
        sign_windows_executable(installer_name, installer_output_dir, True)


###############################
# Remote copy installer
###############################
def remote_copy_installer(optionDict, remote_dest_dir, file_name, installer_output_dir, installer_name_final):
    # ensure remote directory exists
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_dest_dir)
    cmd_args = [optionDict['SCP_COMMAND'], file_name, optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + remote_dest_dir + '/' + installer_name_final]
    bldinstallercommon.do_execute_sub_process(cmd_args, installer_output_dir)


###############################
# Remote copy installer (Opensource)
###############################
def remote_copy_installer_opensource(optionDict, remote_dest_dir, ext_server_base_url, ext_dest_dir, installer_name_final):
    # copy installer to mirror brain server
    if optionDict['LICENSE'] == 'opensource':
        cmd_args_copy_to_pkg = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR']]
        cmd_args_copy_to_ext = cmd_args_copy_to_pkg + ['scp', remote_dest_dir + '/' + installer_name_final, ext_server_base_url + ':' + ext_dest_dir  + '/' + installer_name_final]
        bldinstallercommon.do_execute_sub_process(cmd_args_copy_to_ext, SCRIPT_ROOT_DIR)


###############################
# Helper to generate installer final name
###############################
def generate_installer_final_name(optionDict, file_name):
    suffix = file_name.split(".")[-1]
    installer_name = file_name
    installer_name_base = os.path.splitext(file_name)[0]
    installer_name_final = installer_name_base + '_' + optionDict['BUILD_NUMBER'] + '.' + suffix
    return installer_name, installer_name_base, installer_name_final


###############################
# Update latest link
###############################
def update_latest_link(optionDict, remote_dest_dir, latest_dir):
    cmd_args = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR'], 'ln -sfn', remote_dest_dir, latest_dir]
    bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR)


###############################
# Delete remote directory tree
###############################
def delete_remote_directory_tree(optionDict, remote_dir):
    cmd_args = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR'], 'rm -rf', remote_dir]
    bldinstallercommon.do_execute_sub_process(cmd_args, WORK_DIR)


###############################
# Trigger RTA cases
###############################
def trigger_rta(optionDict, rta_description_files_dir):
    # check if rta cases define for this build job
    if not os.path.isdir(rta_description_files_dir):
        raise IOError('*** Error - Given rta_description_files_dir does not exist: %s' % rta_description_files_dir)
    dir_list = os.listdir(rta_description_files_dir)
    matching = [s for s in dir_list if pkg_constants.RTA_DESCRIPTION_FILE_NAME_BASE in s]
    if not matching:
        print('No RTA cases defined for this build job.')
        return
    # obtain RTA server base url
    if not optionDict['RTA_SERVER_BASE_URL']:
        print('*** Error - RTA_SERVER_BASE_URL env. variable is not defined. Unable to proceed! RTA not run for this build job!')
        return
    rta_server_base_url = optionDict['RTA_SERVER_BASE_URL']
    if not rta_server_base_url.endswith('/'):
        rta_server_base_url += '/'
    # iterate rta description files
    for rta_description_file in matching:
        print('Reading RTA description file: {0}'.format(os.path.join(rta_description_files_dir, rta_description_file)))
        rta_file = os.path.join(rta_description_files_dir, rta_description_file)
        f = open(rta_file)
        for line in iter(f):
            line_split = line.split(' ')
            if len(line_split) != 2:
                raise RuntimeError('*** Error - Invalid format in rta description file %s, line: %s' % (rta_file, line))
            rta_keys = line_split[1].split(',')
            for item in rta_keys:
                item = item.rstrip().replace(' ', '')
                if item:
                    url = rta_server_base_url + item + '/build?token=JENKINS_SQUISH'
                    print('Triggering RTA case: {0}'.format(url))
                    urllib.urlretrieve(url)
        f.close()


################################
# Handle online repository build
################################
def handle_online_repository_build(optionDict):
    conf_file = optionDict['RELEASE_DESCRIPTION_FILE']
    if not os.path.exists(conf_file):
        raise IOError('*** The given file does not exist: %s' % conf_file)
    if optionDict['TARGET_ENV'].find('64') != -1:
        arch = 'x64'
    else:
        arch = 'x86'

    # do we update staging repository?
    update_staging_repo = True
    if optionDict['DO_UPDATE_STAGING_REPOSITORY']:
        update = optionDict['DO_UPDATE_STAGING_REPOSITORY']
        if update.lower() in ['no', 'false', '0']:
            update_staging_repo = False
    # do we also update production repository?
    update_production_repo = False
    if optionDict['DO_UPDATE_PRODUCTION_REPOSITORY']:
        update = optionDict['DO_UPDATE_PRODUCTION_REPOSITORY']
        if update.lower() in ['yes', 'true', '1']:
            update_production_repo = True
    release_build_handler.handle_repo_build(optionDict, 'release', arch, update_staging_repo, update_production_repo)
    # (3) trigger rta cases
    rta_descr_output_dir = os.path.join(SCRIPT_ROOT_DIR, pkg_constants.RTA_DESCRIPTION_FILE_DIR_NAME)
    trigger_rta(optionDict, rta_descr_output_dir)


###############################
# create_remote_dirs
###############################
def create_remote_dirs(optionDict, server, dir_path):
    cmd_args = [optionDict['SSH_COMMAND'], '-t', '-t', server, 'mkdir -p', dir_path]
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)


###############################
# ICU build init
###############################
def initialize_icu_build(optionDict):
    # sanity check icu args in optionDict
    if not optionDict['ICU_VERSION']:
        raise RuntimeError('*** ICU build is missing: icu_version')
    if not optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR']:
        raise RuntimeError('*** ICU build is missing: path')
    if not optionDict['BUILD_NUMBER']:
        raise RuntimeError('*** ICU build is missing: build_number')
    remote_snaphot_dir_base = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + 'icu' + '/' + optionDict['ICU_VERSION']
    remote_snaphot_dir = remote_snaphot_dir_base + '/' + optionDict['BUILD_NUMBER']
    remote_latest_dir = remote_snaphot_dir_base + '/' + 'latest'
    # create remote snapshot dir
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_snaphot_dir)
    # update latest symlink
    update_latest_link(optionDict, remote_snaphot_dir, remote_latest_dir)


###############################
# Handle ICU builds
###############################
def handle_icu_build(optionDict):
    # sanity check icu args in optionDict
    if not optionDict['ICU_VERSION']:
        raise RuntimeError('*** ICU build is missing: ICU_VERSION')
    if not optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR']:
        raise RuntimeError('*** ICU build is missing: PACKAGE_STORAGE_SERVER_BASE_DIR')
    if not optionDict['ICU_SRC_PKG_URL']:
        raise RuntimeError('*** ICU build is missing: ICU_SRC_PKG_URL')
    bld_icu_tools.init_build_icu(optionDict['ICU_SRC_PKG_URL'], optionDict['ICU_VERSION'], True)
    # define remote dir where to upload
    remote_snaphot_dir = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + 'icu' + '/' + optionDict['ICU_VERSION'] + '/' + 'latest'
    srv_and_remote_dir = optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + remote_snaphot_dir
    # check the build artifacts
    local_archives_dir = bldinstallercommon.locate_directory(SCRIPT_ROOT_DIR, ICU_BUILD_OUTPUT_DIR)
    dir_list = os.listdir(local_archives_dir)
    for file_name in dir_list:
        if file_name.endswith('.7z'):
            cmd_args = [optionDict['SCP_COMMAND'], file_name, srv_and_remote_dir + '/']
            bldinstallercommon.do_execute_sub_process(cmd_args, local_archives_dir)


def initPkgOptions(args):
    def mergeTwoDicts(x, y):
        """Given two dicts, merge them into a new dict as a shallow copy."""
        z = x.copy()
        z.update(y)
        return z

    def getDefaultTargetEnv():
        """For local builds define default build target"""
        plat = platform.system().lower()
        if plat.startswith('win'):
            return "win-msvc2015-Windows10-x64"
        elif plat.startswith('linux'):
            return "linux-g++-Rhel6.6-x64"
        elif plat.startswith('darwin'):
            return "mac-clang-10.11-x64"
        else:
            raise RuntimeError("Unsupported host platform")

    optionDict = {}
    # Are we using local conf file for pkg options?
    if args.pkg_conf_file:
        options = optionparser.getPkgOptions(args.pkg_conf_file)
        optionDict = mergeTwoDicts(optionDict, options.configMap())
        optionDict['TARGET_ENV'] = getDefaultTargetEnv()
        optionDict['BUILD_NUMBER'] = str(strftime('%Y%m%d%H%M%S', gmtime()))
        optionDict['PACKAGE_STORAGE_SERVER_ADDR'] = optionDict['PACKAGE_STORAGE_SERVER_USER'] + '@' + optionDict['PACKAGE_STORAGE_SERVER']
        (ifw_dest_dir_name, ifw_dest_dir) = get_ifw_remote_destination_dir(optionDict)
        optionDict['IFW_TOOLS_BASE_URL'] = ifw_dest_dir
    else:
        optionDict = dict(os.environ)
        # Check for command line overrides
        optionDict['LICENSE'] = args.license
        optionDict['PACKAGE_STORAGE_SERVER_ADDR'] = args.server
        optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] = args.path
        optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] = args.path
        optionDict['OPENSSL_LIBS'] = args.openssl_libs
        optionDict['QT_CREATOR_VERSION'] = args.qtcreator_version
        optionDict['QT_CREATOR_VERSION_DESCRIPTION'] = args.qtcreator_version
        optionDict['SNAPSHOT_SERVER'] = args.snapshot_server
        optionDict['SNAPSHOT_SERVER_PATH'] = args.snapshot_path
        optionDict['TARGET_ENV'] = args.target_env if args.target_env else os.environ.get('cfg')
        optionDict['BUILD_NUMBER'] = args.build_number if args.build_number else os.environ.get('BUILD_NUMBER')

    if bldinstallercommon.is_linux_platform():
        optionDict['HOST_PLATFORM'] = 'linux'
    elif bldinstallercommon.is_mac_platform():
        optionDict['HOST_PLATFORM'] = 'mac'
    else:
        optionDict['HOST_PLATFORM'] = 'windows'

    optionDict['CURL_COMMAND'] = '%CURL%' if bldinstallercommon.is_win_platform() else 'curl'
    optionDict['RM_COMMAND'] = '%RM%' if bldinstallercommon.is_win_platform() else 'rm'
    optionDict['SSH_COMMAND'] = '%SSH%' if bldinstallercommon.is_win_platform() else 'ssh'
    optionDict['SCP_COMMAND'] = '%SCP%' if bldinstallercommon.is_win_platform() else 'scp'

    # Parse version number info if this build utilizes release description file
    if 'RELEASE_DESCRIPTION_FILE' in optionDict:
        # Path relative to script or absolute file path
        path = optionDict['RELEASE_DESCRIPTION_FILE']
        path = path if (os.path.isabs(path) and os.path.isfile(path)) else os.path.join(SCRIPT_ROOT_DIR, path)
        optionDict['RELEASE_DESCRIPTION_FILE'] = path
        parser = ConfigParser.ConfigParser()
        parser.read(path)
        for s in parser.sections():
            if s == 'release.global':
                version = bldinstallercommon.safe_config_key_fetch(parser, s, 'version')
                version_tag = bldinstallercommon.safe_config_key_fetch(parser, s, 'version_tag')
                optionDict['VERSION'] = version
                optionDict['VERSION_TAG'] = version_tag
                optionDict['VERSION_FULL'] = version if not version_tag else version + '-' + version_tag
    # generate build slave label/build result target dir dictionary
    generate_bin_target_dictionary()
    return optionDict


if __name__ == '__main__':
    bldinstallercommon.init_common_module(SCRIPT_ROOT_DIR)
    # Define supported build steps
    bld_ifw                  = 'ifw'
    bld_qtcreator            = 'build_creator'
    bld_gammaray             = 'build_gammaray'
    create_online_repository = 'repo_build'
    create_offline_installer = 'offline_installer'
    create_online_installer  = 'online_installer'
    bld_icu_init             = 'init_icu_bld'
    bld_icu                  = 'icu_bld'
    bld_licheck              = 'licheck_bld'
    CMD_LIST =  (bld_ifw, bld_qtcreator, bld_gammaray, bld_icu_init, bld_icu, bld_licheck)
    CMD_LIST += (create_online_installer, create_online_repository, create_offline_installer)

    parser = argparse.ArgumentParser(prog="Build Wrapper", description="Manage all packaging related build steps.")
    parser.add_argument("-c", "--command", dest="command", required=True, choices=CMD_LIST, help=CMD_LIST)
    parser.add_argument("--pkg-conf-file", dest="pkg_conf_file", default="", help="instead of reading various config options from env variables read them from the given file.")
    parser.add_argument("-l", "--license", dest="license", default="", help="license type: enterprise or opensource")
    parser.add_argument("-b", "--build_number", dest="build_number", default="", help="Unique build number identifier")
    parser.add_argument("-s", "--server", dest="server", default="", help="Upload server e.g. <user>@<host>")
    parser.add_argument("-p", "--path", dest="path", default="", help="Path on server")
    parser.add_argument("-e", "--target_env", dest="target_env", default="", help="Target environment: Linux, Linux_64, mac, win")
    parser.add_argument("-o", "--openssl_libs", dest="openssl_libs", default="", help="Url for pre-compiled openssl libraries")
    parser.add_argument("--qtcreator-version", dest="qtcreator_version", default="", help="Qt Creator version, e.g. '3.0.0-rc', used in file names")
    parser.add_argument("--qtcreator-version-description", dest="qtcreator_version_description", default="", help="Qt Creator's version description, e.g. '3.0.0-rc-enterprise', or 'opensource', shown in Qt Creator's about dialog in addition to the version")
    parser.add_argument("--snapshot-server", dest="snapshot_server", default="", help="Additional snapshot upload server <user>@<host> (is uploaded from upload server)")
    parser.add_argument("-snapshot-path", dest="snapshot_path", default="", help="Path on additional snapshot upload server")
    if len(sys.argv) < 2:
        parser.print_usage()
        raise RuntimeError()
    args = parser.parse_args(sys.argv[1:])

    # Init configuration options first
    optionDict = initPkgOptions(args)

    # Execute given command
    # QtCreator specific
    if args.command == bld_qtcreator:
        handle_qt_creator_build(optionDict, parseQtCreatorPlugins(args.pkg_conf_file))
    # GammaRay Qt module
    elif args.command == bld_gammaray:
        handle_gammaray_build(optionDict)
    # Qt Installer-Framework specific
    elif args.command == bld_ifw:
        handle_ifw_build(optionDict)
    # Installer build specific
    elif args.command == create_online_repository:
        handle_online_repository_build(optionDict)
    elif args.command == create_offline_installer:
        handle_offline_installer_build(optionDict)
    elif args.command == create_online_installer:
        handle_online_installer_build(optionDict)
    elif args.command == bld_icu_init:
        initialize_icu_build(optionDict)
    elif args.command == bld_icu:
        handle_icu_build(optionDict)
    elif args.command == bld_licheck:
        handle_qt_licheck_build(optionDict)
    else:
        print('Unsupported command')
