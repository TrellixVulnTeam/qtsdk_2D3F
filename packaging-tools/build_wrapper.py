#!/usr/bin/env python
#############################################################################
##
## Copyright (C) 2020 The Qt Company Ltd.
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
import json
import os
import sys
import re
import shutil
import platform
import urllib
import urlparse
from urllib2 import urlopen
from time import gmtime, strftime
import bld_ifw_tools
from bld_ifw_tools import IfwOptions
import bld_utils
import bldinstallercommon
import batch_process_installer_bld
import bld_icu_tools
import pkg_constants
from pkg_constants import ICU_BUILD_OUTPUT_DIR
from threadedwork import ThreadedWork, Task
import bld_sdktool
from read_remote_config import get_pkg_value


# ----------------------------------------------------------------------
SCRIPT_ROOT_DIR             = os.path.dirname(os.path.realpath(__file__))
REPO_OUTPUT_DIR             = os.path.normpath(os.path.join(SCRIPT_ROOT_DIR, 'repository'))
WORK_DIR                    = os.getenv('PKG_NODE_ROOT') if os.getenv("PKG_NODE_ROOT") else os.path.abspath(os.path.join(__file__, '../../../'))
OPTION_PARSER               = 0
BIN_TARGET_DIRS             = {} # dictionary populated based on the /packaging-tools/releases/build-meta
CI_TARGET_POSTFIX           = {} # dictionary populated based on the /packaging-tools/releases/build-meta
EXTRA_ENV                   = dict(os.environ)
BUILD_META_INFO_FILE        = 'releases/build-meta'
LOCAL_MODE                  = os.getenv('LOCAL_MODE') # if set, installers will be copied to a local directory
LOCAL_INSTALLER_DIR         = os.getenv('LOCAL_INSTALLER_DIR', os.path.join(WORK_DIR, 'installers'))

if LOCAL_MODE:
    assert os.path.exists(LOCAL_INSTALLER_DIR), "Local installer destination directory does not exist: %s" % LOCAL_INSTALLER_DIR
    print("Installer files will be copied to local directory: %s" % LOCAL_INSTALLER_DIR)

###############################
# sign windows executable
###############################
def sign_windows_executable(file_path, working_dir, abort_on_fail):
    signTools = ["signtool32.exe", "keys.pfx", "capicom.dll"]
    signToolsTempDir = r'C:\Utils\sign_tools_temp'
    for item in signTools:
        dst = os.path.join(signToolsTempDir, item)
        curl_cmd_args = ['curl', "--fail", "-L", "--retry", "5", "--retry-delay", "30", "-o", dst, '--create-dirs', "http://ci-files01-hki.intra.qt.io/input/semisecure/sign/current/tools/" + item]
        bldinstallercommon.do_execute_sub_process(curl_cmd_args, working_dir, abort_on_fail)
    cmd_args = [os.path.join(signToolsTempDir, 'signtool32.exe'), 'sign', '/v', '/du', optionDict['SIGNING_SERVER'], '/p', optionDict['SIGNING_PASSWORD'],
                '/tr', 'http://timestamp.digicert.com', '/f', os.path.join(signToolsTempDir, 'keys.pfx'), '/td', "sha256", '/fd', "sha256", file_path]
    args_log = cmd_args[:]
    args_log[6] = "****"
    args_log[8] = "****"
    bldinstallercommon.do_execute_sub_process(cmd_args, working_dir, abort_on_fail, args_log=' '.join(args_log))
    if os.path.exists(signToolsTempDir):
        shutil.rmtree(signToolsTempDir)


###############################
# Unlock keychain script
###############################
def unlock_keychain_script():
    return '/Users/qt/unlock-keychain.sh'

###############################
# Unlock keychain
###############################
def unlock_keychain():
    cmd_args = [unlock_keychain_script()]
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)


###############################
# Lock keychain
###############################
def lock_keychain():
    cmd_args = ['/Users/qt/lock-keychain.sh']
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR, abort_on_fail=False)


###############################
# sign macOS executable
###############################
def sign_mac_executable(file_path, working_dir, abort_on_fail):
    unlock_keychain()
    # "-o runtime" is required for notarization
    cmd_args = ['codesign', '-o', 'runtime', '--verbose=3', '-r', get_pkg_value("SIGNING_FLAGS"), '-s', get_pkg_value("SIGNING_IDENTITY"), file_path]
    bldinstallercommon.do_execute_sub_process(cmd_args, working_dir, abort_on_fail)


###########################################
# init snapshot build dir and upload files
###########################################
def init_snapshot_dir_and_upload_files(optionDict, project_name, project_version_or_branch, build_number, file_upload_list, subdir = ''):
    if subdir is not "" and subdir[0] is not "/":
        subdir = "/" + subdir
    remote_path_base                        = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + project_name + '/' + project_version_or_branch
    remote_path_snapshot_dir                = remote_path_base + '/' + build_number
    remote_path_latest_link                 = remote_path_base + '/' + 'latest'
    # ensure remote directory exists
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_path_snapshot_dir + subdir)
    # upload files
    for item in file_upload_list:
        cmd_args = [optionDict['SCP_COMMAND'], item, optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + remote_path_snapshot_dir + subdir]
        bldinstallercommon.do_execute_sub_process(cmd_args, optionDict['WORK_DIR'])
    # update 'latest' symlink
    update_latest_link(optionDict, remote_path_snapshot_dir, remote_path_latest_link)


###############################
# handle_qt_licheck_build
###############################
def handle_qt_licheck_build(optionDict):
    if optionDict['LICENSE'] == 'enterprise':
        # Build license checker
        exe_dir = optionDict['WORK_DIR'] + '/license-managing/licheck'
        upload_path = optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/licheck/'
        if optionDict['TARGET_ENV'].lower().startswith("win"):
            cmd_args = [r'c:\Utils\jom\jom.exe', '-f', 'Makefile_win']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            cmd_args = [optionDict['SCP_COMMAND'], 'licheck.exe', upload_path]
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
        elif optionDict['TARGET_ENV'].lower().startswith("linux"):
            cmd_args = ['make', '-j6', '-f', 'Makefile_unix']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            licheck = 'licheck64' if optionDict['TARGET_ENV'].find("x64") >= 1 else 'licheck32'
            cmd_args = ['rsync', '-r', licheck, upload_path + licheck]
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
        else:
            cmd_args = ['make', '-j6', '-f', 'Makefile_macos']
            bldinstallercommon.do_execute_sub_process(cmd_args, exe_dir, True)
            cmd_args = ['rsync', '-r', 'licheck_mac', upload_path +'licheck_mac']
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
    if not qt_src_pkg.endswith(('.zip', '.7z', '.tar.gz', '.tar.xz')):
        qt_src_pkg = qt_src_pkg + '.zip' if bldinstallercommon.is_win_platform() else qt_src_pkg + '.tar.gz'
    # Prebuilt static Qt
    qt_static_bin_pkg = optionDict['IFW_STATIC_QT_BIN_PKG']
    # Prebuilt DYNAMIC Qt
    qt_dynamic_bin_pkg = optionDict['IFW_DYNAMIC_QT_BIN_PKG']
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
        product_key_checker_pri = optionDict.get('PRODUCT_KEY_CHECKER_PRI', '')
        if product_key_checker_pri:
            temp = bldinstallercommon.locate_file(optionDict['WORK_DIR'], product_key_checker_pri)
            product_key_checker_pri = temp if temp else product_key_checker_pri

    squish_dir = ''
    if 'ENABLE_TESTS' in optionDict and optionDict['ENABLE_TESTS'] == "true" and bldinstallercommon.is_win_platform():
        squish_dir = 'C:\\Utils\\Squish_IFW'

    # options object for ifw build
    ifw_bld_options = IfwOptions(qt_src_pkg,
                                 qt_configure_options,
                                 ifw_url, ifw_branch,
                                 ifw_qmake_args,
                                 openssl_dir,
                                 product_key_checker_pri,
                                 qt_static_bin_pkg,
                                 qt_dynamic_bin_pkg,
                                 optionDict['SIGNING_SERVER'],
                                 optionDict['SIGNING_PASSWORD'],
                                 False,
                                 squish_dir) # incremental
    # build ifw tools
    bld_ifw_installer = True if 'CREATE_IFW_INSTALLER' in optionDict else False
    bld_ifw_tools.build_ifw(ifw_bld_options, bld_ifw_installer)

    artifacts_dir = os.path.join(SCRIPT_ROOT_DIR, pkg_constants.IFW_BUILD_ARTIFACTS_DIR)

    # create source archive
    archive_name = bldinstallercommon.git_archive_repo(ifw_url + "#" + ifw_branch)
    print("Moving {0} into {1}".format(archive_name, os.path.join(artifacts_dir, optionDict['LICENSE'] + "-", archive_name)))
    shutil.move(archive_name, artifacts_dir)

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
        sign_installer(artifacts_dir, installer_name, installer_name_base, optionDict.get('NOTARIZE'))
    # Upload
    files_to_upload = [f for f in os.listdir(artifacts_dir) if f.endswith((".dmg", ".run", ".exe", ".7z", ".tar.gz", ".zip"))]
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
# Download Qt documentation, extract and repackage with toplevel dir renamed
###############################
def move_files_to_parent_dir(source):
    destination = os.path.abspath(os.path.join(source, os.pardir))
    print("Moving files from: [{0}] to: [{1}]".format(source, destination))
    assert os.path.isdir(source), "The given source is not a directory: %s" % source
    assert os.path.isdir(destination), "The destination is not a directory: %s" % destination
    files_list = os.listdir(source)
    for file in files_list:
        shutil.move(os.path.join(source, file), destination)
    os.rmdir(source)


def create_download_documentation_task(base_url, download_path):
    doc_base_url = base_url + "/doc"

    useLocal = urlparse.urlparse(doc_base_url).scheme == "file"
    print("doc_base_url: {} useLocal: {}".format(doc_base_url, useLocal))
    if useLocal:
        file_list = os.listdir(doc_base_url[len("file:///"):])
    else:
        urlpath = urlopen(doc_base_url)
        string = urlpath.read().decode('utf-8')
        pattern = re.compile('[0-9a-zA-Z-]*.zip')

        file_list = pattern.findall(string)
        file_list = list(dict.fromkeys(file_list))

    # In Qt 6 a zip file with all html files and index files exists.
    # Use it if available.
    if 'qt5-documentation.zip' in file_list:
        file_list = ['qt5-documentation.zip']

    extract_path = os.path.join(download_path, 'tqtc-qt5-documentation')
    target_filepath = os.path.join(download_path, 'qt-everywhere-documentation.7z')

    def create_remove_one_dir_level_function(path):
        return lambda: move_files_to_parent_dir(path)

    def repackage():
        source_path = extract_path
        dest_doc_path = os.path.join(download_path, 'doc')
        os.rename(source_path, dest_doc_path)
        # limit compression to 2 cores to limit memory footprint for 32bit Windows
        bld_utils.runCommand(['7z', 'a', '-mx1', '-mmt2', '-md32m', '-ms=1g', target_filepath, dest_doc_path],
                             dest_doc_path, None)

    download_task = Task("downloading documentation from {0}".format(base_url))
    for item in file_list:
        url = base_url + '/doc/' + item
        download_filepath = os.path.join(download_path, item)
        download_task.addFunction(bld_utils.download, url, download_filepath)
        download_task.addFunction(bldinstallercommon.create_extract_function(download_filepath, extract_path, None))
        download_task.addFunction(create_remove_one_dir_level_function(os.path.join(extract_path, item.rstrip(".zip"))))

    repackage_task = Task("repackaging documentation as {0}".format(target_filepath))
    repackage_task.addFunction(repackage)
    return (download_task, repackage_task, bld_utils.file_url(target_filepath))

def create_download_openssl_task(url, download_path):
    # create openssl 7zips which just contain the DLLs / SOs, so they can just be extracted
    # into the Qt lib directory and later on deployed with Qt
    (_, filename) = os.path.split(url)
    download_filepath = os.path.join(download_path, filename)
    extract_path = os.path.join(download_path, 'openssl_download')
    target_filepath = os.path.join(download_path, 'openssl.7z')

    def repackage():
        listing = os.listdir(extract_path)
        subdircount = len(listing)
        base_path = os.path.join(extract_path, listing[0]) if subdircount >= 1 else extract_path
        win64dir = os.path.join(base_path, 'Win_x64', 'bin')
        win32dir = os.path.join(base_path, 'Win_x86', 'bin')
        linuxdir = os.path.join(base_path, 'openssl', 'lib')
        if os.path.exists(win64dir):
            source_path = win64dir
            pattern = '*.dll'
        elif os.path.exists(win32dir):
            source_path = win32dir
            pattern = '*.dll'
        else:
            source_path = linuxdir
            pattern = '*.so*'
        bld_utils.runCommand(['7z', 'a', '-mmt2', target_filepath, pattern],
                             source_path, None)

    download_task = Task('downloading openssl from {0}'.format(url))
    download_task.addFunction(bld_utils.download, url, download_filepath)
    repackage_task = Task("repackaging openssl as {0}".format(target_filepath))
    repackage_task.addFunction(bldinstallercommon.create_extract_function(download_filepath, extract_path, None))
    repackage_task.addFunction(repackage)
    return (download_task, repackage_task, bld_utils.file_url(target_filepath))

###############################
# handle_gammaray_build
###############################
def handle_gammaray_build(optionDict):
    work_dir = optionDict['WORK_DIR']

    def cloneGammarayRepo(gitUrl, gitBranchOrTag, checkoutDir):
        srcPath = os.path.join(work_dir, checkoutDir)
        if os.path.exists(srcPath):
            shutil.rmtree(srcPath)
        os.makedirs(srcPath)
        bldinstallercommon.clone_repository(gitUrl, gitBranchOrTag, srcPath, init_subrepos=True)

    # Get gammaray sources if not present yet
    if 'GAMMARAY_INTEGRATION_GIT_URL' in optionDict:
        cloneGammarayRepo(optionDict['GAMMARAY_INTEGRATION_GIT_URL'], optionDict['GAMMARAY_INTEGRATION_GIT_BRANCH_OR_TAG'], 'src')
    if 'GAMMARAY_INTEGRATION_OVERRIDE_GAMMARAY_SHA' in optionDict:
        bldinstallercommon.do_execute_sub_process(['git', 'checkout',
                                                   optionDict['GAMMARAY_INTEGRATION_OVERRIDE_GAMMARAY_SHA']],
                                                  os.path.join(work_dir, 'src', 'external', 'gammaray'))

    gammaray_version = optionDict['GAMMARAY_VERSION']
    icu_libs = optionDict.get('ICU_LIBS') # optional
    qt_base_path = optionDict['QT_BASE_PATH']
    qt_module_urls = []

    # qtgamepad is currently needed by Qt3DInput which is currently needed for 3D support in GammaRay
    qt_modules = ['qtbase', 'qtdeclarative', 'qtscript', 'qttools', 'qtxmlpatterns', 'qt3d', 'qtgamepad']
    pkg_base_path = optionDict['PACKAGE_STORAGE_SERVER_PATH_HTTP']
    # Check if the archives reside on network disk (http) or on local file system
    scheme = "" if urlparse.urlparse(pkg_base_path).scheme != "" else "file://"
    qt_base_url = scheme + pkg_base_path + '/' + qt_base_path
    qt_postfix = os.environ['QT_POSTFIX']
    qt_module_urls = [qt_base_url + '/' + module + '/' + module + '-' + qt_postfix + '.7z'
                      for module in qt_modules]

    build_environment = dict(os.environ)
    if bldinstallercommon.is_win_platform() and ('WINDOWS_UNIX_TOOLS_PATH' in optionDict):
        # for awk/gawk, sed, bison, and flex, for internal graphviz build
        path_key = 'Path' if 'Path' in build_environment else 'PATH'
        build_environment[path_key] = ';'.join(['C:\\Windows\\System32', optionDict['WINDOWS_UNIX_TOOLS_PATH'],
                                                build_environment[path_key]])

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
    cmd_args.extend(['--module_dir', os.path.join(work_dir, 'src', 'external', 'kdstatemachineeditor'),
                     '--module-name', 'kdsme',
                     '--qt5path', os.path.join(work_dir, 'kdsme_qt5_install'),
                     '--add-config-arg=-DBUILD_EXAMPLES=OFF',
                     '--add-config-arg=-DBUILD_TESTING=OFF',
                     '--add-config-arg=-DWITH_INTERNAL_GRAPHVIZ=ON'])
    bldinstallercommon.do_execute_sub_process(cmd_args, work_dir, extra_env=build_environment)

    # build gammaray
    cmd_args = common_gammaray_args()
    cmd_args.extend(['--module_dir', os.path.join(work_dir, 'src', 'external', 'gammaray'),
                     '--module-name', 'gammaray',
                     '--qt5path', os.path.join(work_dir, 'gammaray_qt5_install'),
                     '--add-config-arg=-DGAMMARAY_INSTALL_QT_LAYOUT=ON',
                     '--add-config-arg=-DCMAKE_SKIP_BUILD_RPATH=OFF',
                     '--add-config-arg=-DCMAKE_BUILD_WITH_INSTALL_RPATH=OFF',
                     '--add-config-arg=-DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE',
                     '--add-config-arg=-DGAMMARAY_MULTI_BUILD=OFF',
                     '--add-config-arg=-DBUILD_TESTING=OFF',
                     '--qt5_module_url', bld_utils.file_url(os.path.join(SCRIPT_ROOT_DIR, 'module_archives', 'qt5_kdsme.7z'))])
    bldinstallercommon.do_execute_sub_process(cmd_args, work_dir)

    # upload
    base_path = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/gammaray/' + gammaray_version
    base_upload_path = base_path + '/' + optionDict['BUILD_NUMBER']
    upload_path = base_upload_path + '/' + optionDict['QTC_PLATFORM']
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

QtcPlugin = collections.namedtuple('QtcPlugin', ['name', 'path', 'version', 'dependencies', 'modules',
                                                 'additional_arguments', 'qmake_arguments', 'build'])
def make_QtcPlugin(name, path, version, dependencies=None, modules=None,
                   additional_arguments=None, qmake_arguments=None, build=True):
    return QtcPlugin(name=name, path=path, version=version,
                     dependencies=dependencies or [],
                     modules=modules or [],
                     additional_arguments=additional_arguments or [],
                     qmake_arguments=qmake_arguments or [],
                     build=build)

class BuildLog:
    def __init__(self, log_filepath, log_overwrite=False):
        self.file = None
        self.log_filepath = log_filepath
        self.log_overwrite = log_overwrite

    def __enter__(self):
        try:
            self.file = open(self.log_filepath, 'w' if self.log_overwrite else 'a')
        except Exception:
            print('Failed to write log file "' + self.log_filepath + '"')
            sys.stdout.flush()
            raise
        return self.file

    def __exit__(self, type, value, traceback):
        self.file.close()
        if type:  # exception raised -> print the log and re-raise
            with open(self.log_filepath, 'r') as f:
                print(f.read())
            return True  # re-raise

# writes output of process to log_filepath
# on error it dumps the log file to stdout as well
def check_call_log(args, execution_path, extra_env=dict(os.environ),
                   log_filepath=None, log_overwrite=False):
    if not log_filepath:
        bldinstallercommon.do_execute_sub_process(args, execution_path,
                                                  extra_env=extra_env)
    else:
        with BuildLog(log_filepath, log_overwrite) as f:
            bldinstallercommon.do_execute_sub_process(args, execution_path,
                                                      extra_env=extra_env,
                                                      redirect_output=f)

def create_qtcreator_source_package(source_path, plugin_name, version, edition, target_path, log_filepath):
    namepart = '-' + plugin_name if plugin_name else ''
    file_base = 'qt-creator-' + edition + namepart + '-src-' + version
    target_base = os.path.join(target_path, file_base)
    create_tar = bldinstallercommon.is_linux_platform()
    create_zip = bldinstallercommon.is_win_platform() and '64' not in optionDict['TARGET_ENV']
    if create_tar or create_zip:
        if not os.path.exists(target_base):
            os.makedirs(target_base)
        bldinstallercommon.copy_tree(source_path, target_base)
        if create_tar:
            check_call_log(['tar', 'czf', file_base + '.tar.gz', '--exclude', '.git', file_base],
                           target_path, log_filepath=log_filepath)
            check_call_log(['tar', 'cJf', file_base + '.tar.xz', '--exclude', '.git', file_base],
                           target_path, log_filepath=log_filepath)
        if create_zip:
            check_call_log(['7z', 'a', '-tzip', file_base + '.zip', file_base, '-xr!.git'],
                           target_path, log_filepath=log_filepath)

def build_qtcreator_plugins(plugins, qtcreator_path, qtcreator_dev_path, icu_url=None,
                            openssl_url=None, log_filepath=None, use_cmake=False):
    work_dir = optionDict['WORK_DIR']
    for plugin in plugins:
        plugin_path = os.path.join(work_dir, plugin.path)
        if not plugin.build or not os.path.isdir(plugin_path):
            continue
        modules = plugin.modules
        cmd_arguments = ['python', '-u']
        if use_cmake:
            build_path = os.path.join(work_dir, plugin.name + '-build')
            qt_path = os.path.join(build_path, 'qt')
            cmd_arguments += [os.path.join(qtcreator_path, 'scripts', 'build_plugin.py'),
                              '--name', plugin.name,
                              '--src', plugin_path,
                              '--build', build_path,
                              '--qt-path', qt_path,
                              '--qtc-path', qtcreator_path,
                              '--output-path', work_dir]
            for dependency_name in plugin.dependencies:
                matches = [dep for dep in plugins if dep.name == dependency_name]
                if not matches:
                    raise RuntimeError('did not find dependency "{0}" for plugin "{1}"'.format(dependency_name, plugin.name))
                dependency = matches[0]
                cmd_arguments.extend(['--add-path', os.path.join(work_dir, dependency.name + '-build', 'build')])
                for module in dependency.modules:
                    if module not in modules:
                        modules.append(module)
            cmd_arguments.extend(plugin.additional_arguments)
        else:
            cmd_arguments = ['python', '-u', os.path.join(SCRIPT_ROOT_DIR, 'bld_qtcreator_plugins.py'),
                             '--clean',
                             '--qtc-build', qtcreator_path,
                             '--qtc-dev', qtcreator_dev_path,
                             '--plugin-path', plugin_path,
                             '--build-path', work_dir]
            if bldinstallercommon.is_win_platform():
                cmd_arguments.extend(['--buildcommand', os.path.normpath('C:/Utils/jom/jom.exe'),
                                      '--installcommand', os.path.normpath('nmake.exe')])
            else:
                cmd_arguments.extend(['--installcommand', 'make -j1'])
            cmd_arguments.extend(plugin.additional_arguments)
            for qmake_arg in plugin.qmake_arguments:
                cmd_arguments.extend(['--add-qmake-argument', qmake_arg])

            if icu_url:
                cmd_arguments.extend(['--icu7z', icu_url])
            if openssl_url:
                cmd_arguments.extend(['--openssl7z', openssl_url])
            for module in modules:
                cmd_arguments.extend(['--qt-module', module])
            cmd_arguments.extend(['--out-dev', os.path.join(work_dir, plugin.name + '_dev.7z')])
            cmd_arguments.append('--cleanup')
            libs_paths = []
            for dependency_name in plugin.dependencies:
                matches = [dep for dep in plugins if dep.name == dependency_name]
                if not matches:
                    raise RuntimeError('did not find dependency "{0}" for plugin "{1}"'.format(dependency_name, plugin.name))
                dependency = matches[0]
                cmd_arguments.extend(['--plugin-search-path', os.path.join(work_dir, dependency.path, 'plugins')])
                libs_base = os.path.join(work_dir, dependency.name + '-target')
                if bldinstallercommon.is_mac_platform():
                    libs_paths.append(os.path.join(libs_base, 'PlugIns'))
                else:
                    libs_paths.append(os.path.join(libs_base, 'lib', 'qtcreator', 'plugins'))
                for module in dependency.modules:
                    if module not in modules:
                        modules.append(module)
            if libs_paths:
                cmd_arguments.extend(['--add-qmake-argument', 'LIBS*=' + ' '.join(['-L' + path for path in libs_paths])])
            cmd_arguments.append(os.path.join(work_dir, plugin.name + '.7z'))

        if use_cmake: # install qt
            qt_install_args = ['python', '-u', os.path.join(SCRIPT_ROOT_DIR, 'install_qt.py'),
                               '--qt-path', qt_path, '--temp-path', os.path.join(build_path, 'temp')]
            for module in modules:
                qt_install_args.extend(['--qt-module', module])
            if bldinstallercommon.is_linux_platform() and icu_url:
                qt_install_args.extend(['--icu7z', icu_url])
            if openssl_url:
                qt_install_args.extend(['--openssl7z', openssl_url])
            check_call_log(qt_install_args, work_dir)

        check_call_log(cmd_arguments, work_dir, log_filepath=log_filepath)
        create_qtcreator_source_package(os.path.join(work_dir, plugin.path), plugin.name, plugin.version,
                                        'enterprise', work_dir, log_filepath)

def get_qtcreator_version(path_to_qtcreator_src, optionDict):
    expr = re.compile(r'\s*QTCREATOR_DISPLAY_VERSION\s*=\s*([^\s]+)')

    default_version_locations = [os.path.join(path_to_qtcreator_src, 'qtcreator_ide_branding.pri'),  # used since QtCreator 4.10
                                 os.path.join(path_to_qtcreator_src, 'qtcreator.pri')]  # old path till 4.9, can be removed in the future
    default_version_location = next(p for p in default_version_locations if os.path.exists(p))
    branding_version_location = optionDict.get('IDE_BRANDING_PRI')  # optional
    version_location = branding_version_location if branding_version_location else default_version_location

    with open(version_location, 'r') as f:
        for line in f:
            match = expr.match(line)
            if match:
                return match.group(1)
    return None

def make_QtcPlugin_from_json(plugin_json):
    return QtcPlugin(name=plugin_json['Name'],
                     path=plugin_json['Path'],
                     version=plugin_json.get('Version'),
                     dependencies=plugin_json.get('Dependencies') or [],
                     modules=plugin_json.get('Modules') or [],
                     additional_arguments=plugin_json.get('AdditionalArguments') or [],
                     qmake_arguments=plugin_json.get('QmakeArguments') or [],
                     build=plugin_json.get('Build') or True)

def parse_qt_creator_plugin_conf(plugin_conf_file_path, optionDict):
    data = {}
    with open(plugin_conf_file_path, 'r') as f:
        data = json.load(f)
    plugins_json = data['Plugins']
    if bldinstallercommon.is_linux_platform():
        platform_name = 'linux'
    elif bldinstallercommon.is_win_platform():
        platform_name = 'windows'
    else:
        platform_name = 'mac'

    def valid_for_platform(plugin_json):
        platforms = plugin_json.get('Platforms')
        return not platforms or platform_name in platforms

    def fixup_plugin(plugin):
        plugin = plugin._replace(modules = [module % optionDict for module in plugin.modules])
        plugin = plugin._replace(qmake_arguments = [arg % optionDict for arg in plugin.qmake_arguments])
        plugin = plugin._replace(additional_arguments = [arg % optionDict for arg in plugin.additional_arguments])
        return plugin
    return [fixup_plugin(make_QtcPlugin_from_json(plugin)) for plugin in plugins_json if valid_for_platform(plugin)]

def collect_qt_creator_plugin_sha1s(plugins):
    work_dir = optionDict['WORK_DIR']
    sha1s = []
    for name in [p.name for p in plugins if p.build and os.path.isdir(os.path.join(work_dir, p.path))]:
        with open(os.path.join(work_dir, name + '.7z.git_sha'), 'r') as f:
            sha = f.read().strip()
            sha1s.append(name + ': ' + sha)
    return sorted(sha1s)


# file_upload_list: list of 2-tuples,
# (source_file_relative_to_WORK_DIR, target_file_or_path_relative_to_remote_path)
def upload_files(remote_path, file_upload_list, optionDict):
    # prepare remote paths
    pkg_storage_server = optionDict['PACKAGE_STORAGE_SERVER_ADDR']
    target_env_dir = optionDict['QTC_PLATFORM']
    latest_path = remote_path + '/latest'
    dir_path = remote_path + '/' + optionDict['BUILD_NUMBER']
    create_remote_dirs(optionDict, pkg_storage_server, dir_path + '/' + target_env_dir)
    update_latest_link(optionDict, dir_path, latest_path)
    # upload files
    for source, destination in file_upload_list:
        cmd_args = [optionDict['SCP_COMMAND'], source, pkg_storage_server + ':' + dir_path + '/' + destination]
        bldinstallercommon.do_execute_sub_process(cmd_args, optionDict['WORK_DIR'])

def update_job_link(remote_path_base, remote_target_path, optionDict):
    remote_link = remote_path_base + '/' + optionDict['PULSE_PROJECT']
    update_latest_link(optionDict, remote_target_path, remote_link)

def handle_qt_creator_plugins_build(optionDict, plugin_conf_file_path):
    target_env_dir = optionDict['QTC_PLATFORM']
    work_dir = optionDict['WORK_DIR']
    optionDict['TARGET_ENV_DIR'] = target_env_dir # inject for plugin configs
    download_temp = os.path.join(work_dir, 'downloads')
    upload_base_path = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['UPLOAD_BASE_DIR']

    plugins = parse_qt_creator_plugin_conf(plugin_conf_file_path, optionDict)
    print('Building plugins:')
    for plugin in plugins:
        print(plugin)

    # fixup module urls for qt modules which are only specified by name
    def module_url(module):
        if '/' not in module and '.7z' not in module: # qt module name
            return optionDict['QT_BASE_URL'] + '/' + module + '/' + module + '-' + optionDict['QT_POSTFIX'] + '.7z'
        return module

    def fixup_plugin(plugin):
        return plugin._replace(modules = [module_url(module) for module in plugin.modules])
    plugins = [fixup_plugin(plugin) for plugin in plugins]

    # download
    ## TODO download Qt modules once and use local url for plugin build
    download_packages_work = ThreadedWork('Get and extract all needed packages')

    ## base Qt Creator
    qtcreator_base_url = optionDict['QTC_BASE_URL'] + '/' + target_env_dir + '/'
    qtcreator_path = os.path.join(work_dir, 'downloads', 'qtc_build')
    qtcreator_dev_path = os.path.join(work_dir, 'downloads', 'qtc_dev')
    download_packages_work.addTaskObject(bldinstallercommon.create_download_extract_task(
        qtcreator_base_url + 'qtcreator.7z', qtcreator_path, download_temp, None))
    download_packages_work.addTaskObject(bldinstallercommon.create_download_extract_task(
        qtcreator_base_url + 'qtcreator_dev.7z', qtcreator_dev_path, download_temp, None))

    ## plugins that are not part of the actual build
    for plugin in [plugin for plugin in plugins if not plugin.build]:
        download_packages_work.addTaskObject(bldinstallercommon.create_download_extract_task(
            qtcreator_base_url + plugin.name + '.7z',
            os.path.join(work_dir, plugin.path + '-target'),
            download_temp, None))
        download_packages_work.addTaskObject(bldinstallercommon.create_download_extract_task(
            qtcreator_base_url + plugin.name + '_dev.7z',
            os.path.join(work_dir, plugin.path),
            download_temp, None))

    icu_libs = optionDict.get('ICU_LIBS') # optional
    icu_path = os.path.join(download_temp, os.path.basename(icu_libs)) if icu_libs else None
    if icu_libs:
        download_packages_work.addTaskObject(bldinstallercommon.create_download_task(
            icu_libs, download_temp))
    icu_local_url = bld_utils.file_url(icu_path) if icu_libs else None

    openssl_libs = optionDict.get('OPENSSL_LIBS') # optional
    openssl_path = os.path.join(download_temp, os.path.basename(openssl_libs)) if openssl_libs else None
    if openssl_libs:
        download_packages_work.addTaskObject(bldinstallercommon.create_download_task(
            openssl_libs, download_temp))
    openssl_local_url = bld_utils.file_url(openssl_path) if openssl_libs else None

    download_packages_work.run()

    # final fixup
    qtcreator_version = get_qtcreator_version(qtcreator_dev_path, optionDict)
    print('Qt Creator version: ' + qtcreator_version)

    def fixup_version(plugin):
        plugin = plugin._replace(version = plugin.version if plugin.version else qtcreator_version)
        return plugin
    plugins = [fixup_version(plugin) for plugin in plugins]

    # build
    build_qtcreator_plugins(plugins, qtcreator_path, qtcreator_dev_path,
                            icu_local_url, openssl_local_url)

    if bldinstallercommon.is_linux_platform():
        # summary of git SHA1s
        sha1s = collect_qt_creator_plugin_sha1s(plugins)
        with open(os.path.join(work_dir, 'SHA1'), 'w') as f:
            f.writelines([sha + '\n' for sha in sha1s])

    # upload lists
    file_upload_list = []
    for plugin in [plugin for plugin in plugins if plugin.build]:
        plugin_name = plugin.name + '.7z'
        plugin_dev_name = plugin.name + '_dev.7z'
        if os.path.isfile(os.path.join(work_dir, plugin_name)):
            file_upload_list.append((plugin_name, target_env_dir + '/' + plugin_name))
        if os.path.isfile(os.path.join(work_dir, plugin_dev_name)):
            file_upload_list.append((plugin_dev_name, target_env_dir + '/' + plugin_dev_name))
    if bldinstallercommon.is_linux_platform():
        ## source packages
        source_package_list = glob(os.path.join(work_dir, 'qt-creator-*-src-' + qtcreator_version + '.*'))
        file_upload_list.extend([(fn, '') for fn in source_package_list])
        # summary of git SHA1s
        file_upload_list.append(('SHA1', ''))

    upload_path = upload_base_path + '/' + qtcreator_version
    upload_files(upload_base_path + '/' + qtcreator_version, file_upload_list, optionDict)
    update_job_link(upload_base_path, upload_path, optionDict)

###############################
# handle_qt_creator_build
###############################
def handle_qt_creator_build(optionDict, qtCreatorPlugins):
    target_env_dir = optionDict['QTC_PLATFORM']
    work_dir = optionDict['WORK_DIR']
    build_environment = dict(os.environ)
    # remove LLVM_INSTALL_DIR set by COIN templates
    build_environment.pop('LLVM_INSTALL_DIR', None)

    # Get Qt Creator sources if not present yet
    if 'QT_CREATOR_GIT_URL' in optionDict:
        qtCreatorSourceDirectory = os.path.join(work_dir, 'qt-creator')
        if os.path.exists(qtCreatorSourceDirectory):
            shutil.rmtree(qtCreatorSourceDirectory)
        os.makedirs(qtCreatorSourceDirectory)
        bldinstallercommon.clone_repository(optionDict['QT_CREATOR_GIT_URL'], optionDict['QT_CREATOR_GIT_BRANCH'],
                                            qtCreatorSourceDirectory, full_clone=True, init_subrepos=True)
    # Get Qt Creator plugin sources if not present yet
    for pluginConf in qtCreatorPlugins:
        checkoutDir = os.path.join(work_dir, pluginConf.checkout_dir)
        if pluginConf.git_url:
            if os.path.exists(checkoutDir):
                shutil.rmtree(checkoutDir)
            os.makedirs(checkoutDir)
            bldinstallercommon.clone_repository(pluginConf.git_url, pluginConf.branch_or_tag, checkoutDir, full_clone=True)

    # Build time variables
    qtcreator_source = os.path.join(work_dir, 'qt-creator')
    qtcreator_version = get_qtcreator_version(qtcreator_source, optionDict)
    pkg_base_path = optionDict['PACKAGE_STORAGE_SERVER_PATH_HTTP']
    # Check if the archives reside on network disk (http) or on local file system
    scheme = "" if urlparse.urlparse(pkg_base_path).scheme != "" else "file://"
    pkg_base_path = scheme + pkg_base_path
    pkg_storage_server = optionDict['PACKAGE_STORAGE_SERVER_ADDR']
    qtcreator_edition_name = optionDict.get('QT_CREATOR_EDITION_NAME') # optional
    unversioned_base_path = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['QTC_BASE_DIR']
    base_path = unversioned_base_path + '/' + qtcreator_version
    if qtcreator_edition_name:
        base_path += '_' + qtcreator_edition_name
    snapshot_server = optionDict.get('SNAPSHOT_SERVER') # optional
    snapshot_path = optionDict['SNAPSHOT_SERVER_PATH'] # optional
    qt_base_path = optionDict['QTC_QT_BASE_DIR']
    ide_branding_pri = optionDict.get('IDE_BRANDING_PRI') # optional
    ide_branding_path = optionDict.get('IDE_BRANDING_PATH') # optional
    ide_branding_app_name = optionDict.get('IDE_BRANDING_APP_NAME') # optional
    installer_patch = optionDict.get('INSTALLER_PATCH') # optional
    skip_cdb = optionDict.get('SKIP_CDB') # optional
    skip_dmg = optionDict.get('SKIP_DMG') # optional
    build_id = optionDict['BUILD_NUMBER']
    icu_libs = optionDict.get('ICU_LIBS') # optional
    openssl_libs = optionDict.get('OPENSSL_LIBS') # optional
    openssl_local_url = None # optionally defined later
    qt_extra_module_url = optionDict.get('QT_EXTRA_MODULE_URL') # optional
    qt_postfix = os.environ['QT_POSTFIX']
    sdktool_base = optionDict.get('SDKTOOL_QTBASESRC_BASE') # optional
    sdktool_ext = optionDict.get('SDKTOOL_QTBASESRC_EXT') # optional
    sdktool_qtbase_src = sdktool_base + sdktool_ext if sdktool_base and sdktool_ext else None # optional
    download_temp = os.path.join(work_dir, 'downloads')
    has_unlock_keychain_script = os.path.exists(unlock_keychain_script())
    # from 4.4 on we use external elfutil builds and also build on Windows
    elfutils_url = optionDict.get('ELFUTILS_URL')
    log_filepath = os.path.join(work_dir, 'build_log.txt')
    notarize = optionDict.get('NOTARIZE')
    use_cmake = optionDict.get('USE_CMAKE')
    usp_server_url = optionDict.get('USP_SERVER_URL')
    usp_auth_key = optionDict.get('USP_AUTH_KEY')
    qtc_additional_config = optionDict.get('QTC_ADDITIONAL_CONFIG') # optional
    disable_docs = optionDict.get('DISABLE_DOCS') # optional
    if qtc_additional_config:
        qtc_additional_config = qtc_additional_config.split()
    qt_temp = os.path.join(work_dir, 'qt-creator_temp') if not use_cmake else os.path.join(work_dir, 'qt_temp')

    def module_filename(module):
        return module + '-' + qt_postfix + '.7z'

    def module_urls(modules):
        return [pkg_base_path + '/' + qt_base_path + '/' + module + '/' + module_filename(module)
                for module in modules]

    # Define paths for pre-built kdsme and gammaray packages
    kdsme_url = optionDict.get("GAMMARAY_BASE_DIR")
    if kdsme_url:
        kdsme_url = (pkg_base_path + '/' + kdsme_url + '/' + target_env_dir + '/qt5_kdsme.7z')
    gammaray_url = optionDict.get("GAMMARAY_BASE_DIR")
    if gammaray_url:
        gammaray_url = (pkg_base_path + '/' + gammaray_url + '/' + target_env_dir + '/qt5_gammaray.7z')

    download_work = ThreadedWork('Download packages')
    extract_work = Task('Extract packages')

    def add_download_extract(url, target_path):
        (download, extract) = bldinstallercommon.create_download_and_extract_tasks(
            url, target_path, download_temp, None)
        download_work.addTaskObject(download)
        extract_work.addFunction(extract.do)

    # clang package
    use_optimized_libclang = False
    llvm_install_dir = None
    clang_filebase = optionDict.get('CLANG_FILEBASE')
    clang_platform = optionDict.get('CLANG_PLATFORM')
    if clang_filebase and clang_platform:
        clang_extract_path = os.path.join(download_temp, 'libclang')
        llvm_install_dir = os.path.join(clang_extract_path, 'libclang') # package contains libclang subdir
        clang_suffix = optionDict.get('CLANG_FILESUFFIX')
        clang_suffix = clang_suffix if clang_suffix is not None else ''
        clang_url = (pkg_base_path + '/' + optionDict['CLANG_FILEBASE'] + '-' + clang_platform + clang_suffix + '.7z')
        add_download_extract(clang_url, clang_extract_path)
        use_optimized_libclang = bldinstallercommon.is_win_platform()
        if use_optimized_libclang:
            postfix = '64' if '64' in optionDict['TARGET_ENV'] else '32'
            opt_clang_url = (pkg_base_path + '/' + optionDict['CLANG_FILEBASE'] + '-windows-mingw_' + postfix + clang_suffix + '.7z')
            opt_clang_path = os.path.join(download_temp, 'opt_libclang')
            opt_clang_lib = os.path.join(opt_clang_path, 'libclang', 'bin', 'libclang.dll')
            add_download_extract(opt_clang_url, opt_clang_path)

    elfutils_path = None
    if elfutils_url:
        elfutils_path = os.path.join(download_temp, 'elfutils')
        add_download_extract(elfutils_url, elfutils_path)

    python_path = None
    python_url = optionDict.get('PYTHON_URL')
    if bldinstallercommon.is_win_platform() and python_url:
        python_path = os.path.join(download_temp, 'python')
        add_download_extract(python_url, python_path)

    # Documentation package for cross-references to Qt.
    # Unfortunately this doesn't follow the normal module naming convention.
    # We have to download, unpack, and repack renaming the toplevel directory.
    (download, repackage, documentation_local_url) = create_download_documentation_task(
        pkg_base_path + '/' + qt_base_path, os.path.join(download_temp, 'qtdocumentation'))
    download_work.addTaskObject(download)
    extract_work.addFunction(repackage.do)

    if openssl_libs:
        (download, repackage, openssl_local_url) = create_download_openssl_task(openssl_libs, os.path.join(download_temp, 'openssl'))
        download_work.addTaskObject(download)
        extract_work.addFunction(repackage.do)

    download_packages_work = Task('Get and extract all needed packages')
    download_packages_work.addFunction(download_work.run)
    download_packages_work.addFunction(extract_work.do)
    download_packages_work.do()

    # copy optimized clang package
    if use_optimized_libclang:
        target_libclang_dll = os.path.join(llvm_install_dir, 'bin', 'libclang.dll')
        shutil.copyfile(opt_clang_lib, target_libclang_dll)

    # Qt Creator build depends on pre-built Qt binary packages.
    # Define the exact archive locations for each required module.
    qt_modules = ['qt5compat', 'qtbase', 'qtdeclarative', 'qtgraphicaleffects',
                  'qtimageformats', 'qtlocation', 'qtmacextras',
                  'qtquick3d', 'qtquickcontrols', 'qtquickcontrols2', 'qtquicktimeline',
                  'qtscript', 'qtserialport', 'qtshadertools', 'qtsvg', 'qttools',
                  'qttranslations', 'qtx11extras', 'qtxmlpatterns']
    qt_module_urls = module_urls(qt_modules)
    qt_module_urls.append(documentation_local_url)
    if qt_extra_module_url:
        qt_module_urls.append(qt_extra_module_url)
    qt_module_local_urls = [bld_utils.file_url(os.path.join(qt_temp, os.path.basename(url)))
                            for url in qt_module_urls]

    # download and install qt
    qt_path = os.path.join(work_dir, 'qt5_install_dir')
    src_path = os.path.join(work_dir, 'qt-creator')
    build_path = os.path.join(work_dir, 'qt-creator_build')
    qt_install_args = ['python', '-u', os.path.join(SCRIPT_ROOT_DIR, 'install_qt.py'),
                       '--qt-path', qt_path, '--temp-path', qt_temp]
    for module_url in qt_module_urls:
        qt_install_args.extend(['--qt-module', module_url])
    if bldinstallercommon.is_linux_platform() and icu_libs:
        qt_install_args.extend(['--icu7z', icu_libs])
    if bldinstallercommon.is_win_platform():
        d3d_url = optionDict['D3D_URL']
        opengl_url = optionDict['OPENGLSW_URL']
        qt_install_args.extend(['--d3dcompiler7z', d3d_url,
                                '--opengl32sw7z', opengl_url])
    if openssl_local_url:
        qt_install_args.extend(['--openssl7z', openssl_local_url])
    check_call_log(qt_install_args,
                   work_dir)
    # Define Qt Creator build script arguments
    renaming_qmake_arguments = []
    cmd_args = ['python', '-u']
    if use_cmake:
        cmd_args += [os.path.join(src_path, 'scripts', 'build.py'),
                     '--src', src_path,
                     '--build', build_path,
                     '--qt-path', qt_path]
        if llvm_install_dir:
            cmd_args += ['--llvm-path', llvm_install_dir]
        if ide_branding_path:
            cmd_args += ['--add-module-path', os.path.abspath(ide_branding_path)]
        if ide_branding_app_name:
            cmd_args += ['--app-target', ide_branding_app_name]
        if qtc_additional_config:
            cmd_args += ['--add-config=' + value for value in qtc_additional_config]
        if disable_docs:
            cmd_args += ['--no-docs']
        if skip_dmg:
            cmd_args += ['--no-dmg']
        keychain_arg = '--keychain-unlock-script'
        python_arg = '--python-path'
        elfutils_arg = '--elfutils-path'
        cdb_arg = '--no-cdb'
        for key in ['SIGNING_IDENTITY', 'SIGNING_FLAGS']:
            try:
                value = get_pkg_value(key)
                # for python2 this is unicode which is not accepted as environment
                if value and type(value) is not str:
                    value = value.encode('UTF-8')
                if value:
                    build_environment[key] = value
            except Exception:
                pass
    else:
        cmd_args = ['python', '-u', os.path.normpath(os.path.join(SCRIPT_ROOT_DIR, 'bld_qtcreator.py')),
                    '--qt5path', qt_path]
        if not bldinstallercommon.is_win_platform():
            cmd_args.extend(['--installcommand', 'make -j1'])
        else:
            cmd_args.extend(['--buildcommand', os.path.normpath('C:/Utils/jom/jom.exe'),
                             '--installcommand', os.path.normpath('nmake.exe')])
        if ide_branding_pri:
            renaming_qmake_arguments.append('IDE_BRANDING_PRI=' + ide_branding_pri)
        cmd_args.extend([arg for value in renaming_qmake_arguments for arg in ['--add-qmake-argument', value]])
        if llvm_install_dir:
            build_environment['LLVM_INSTALL_DIR'] = llvm_install_dir
        keychain_arg = '--keychain_unlock_script'
        python_arg = '--python_path'
        elfutils_arg = '--elfutils_path'
        cdb_arg = '--skip_cdb'

    if bldinstallercommon.is_mac_platform():
        if has_unlock_keychain_script:
            cmd_args.extend([keychain_arg, unlock_keychain_script()])
    if python_path:
        cmd_args.extend([python_arg, python_path])
    if elfutils_path:
        cmd_args.extend([elfutils_arg, elfutils_path])
    if skip_cdb:
        cmd_args.append(cdb_arg)
    check_call_log(cmd_args, work_dir, extra_env=build_environment, log_filepath=log_filepath, log_overwrite=True)

    if bldinstallercommon.is_mac_platform() and has_unlock_keychain_script:
        lock_keychain()

    # Qt Creator plugins
    # TODO cmake: dependencies
    plugin_dependencies = []
    qmake_arguments = list(renaming_qmake_arguments)
    additional_plugins = []

    if os.path.isdir(os.path.join(work_dir, "licensechecker")):
        add_args = ['--deploy'] if not use_cmake else ['--add-path', os.path.join(work_dir, 'license-managing')]
        additional_plugins.extend([make_QtcPlugin('licensechecker', 'licensechecker', qtcreator_version,
                                                  modules=qt_module_local_urls,
                                                  qmake_arguments=renaming_qmake_arguments,
                                                  additional_arguments=add_args)])
        qmake_arguments.append('CONFIG+=licensechecker')
        plugin_dependencies = ['licensechecker']
    additional_plugins.extend([make_QtcPlugin('vxworks-qtcreator-plugin', 'vxworks-qtcreator-plugin', qtcreator_version,
                                              modules=qt_module_local_urls, dependencies=plugin_dependencies,
                                              qmake_arguments=qmake_arguments)])
    additional_plugins.extend([make_QtcPlugin('isoiconbrowser', 'qtquickdesigner', qtcreator_version,
                                              modules=qt_module_local_urls, dependencies=plugin_dependencies,
                                              qmake_arguments=qmake_arguments)])
    additional_plugins.extend([make_QtcPlugin('gammarayintegration', 'gammarayintegration', qtcreator_version,
                                              modules=qt_module_local_urls + [kdsme_url, gammaray_url] + module_urls(['qt3d', 'qtgamepad']),
                                              dependencies=plugin_dependencies,
                                              qmake_arguments=qmake_arguments,
                                              additional_arguments=[
                                                  '--deploy-command', 'python',
                                                  '--deploy-command=-u',
                                                  '--deploy-command', os.path.join(work_dir, 'gammarayintegration', 'scripts', 'deploy.py')
                                              ] if not use_cmake else ['--deploy'])])
    additional_plugins.extend([make_QtcPlugin('appmanagerintegration', 'pcore-plugin-appman', qtcreator_version,
                                              modules=qt_module_local_urls,
                                              dependencies=plugin_dependencies,
                                              qmake_arguments=qmake_arguments)]),
    plugin_telemetry_args = []
    if use_cmake and usp_server_url and usp_auth_key:
        plugin_telemetry_args = ['--add-config=-DUSP_SERVER_URL=' + optionDict['USP_SERVER_URL'],
                                 '--add-config=-DUSP_AUTH_KEY=' + optionDict['USP_AUTH_KEY']]
    additional_plugins.extend([make_QtcPlugin('plugin-telemetry', 'plugin-telemetry', qtcreator_version,
                                              modules=qt_module_local_urls,
                                              additional_arguments=plugin_telemetry_args)]),

    # Build Qt Creator plugins
    icu_local_url = bld_utils.file_url(os.path.join(qt_temp, os.path.basename(icu_libs))) if bldinstallercommon.is_linux_platform() else None
    ## extract qtcreator bin and dev packages
    qtcreator_path = os.path.join(work_dir, 'qtc_build')
    qtcreator_dev_path = os.path.join(work_dir, 'qtc_dev') if not use_cmake else qtcreator_path
    check_call_log(['7z', 'x', '-y', os.path.join(work_dir, 'qt-creator_build', 'qtcreator.7z'), '-o' + qtcreator_path],
                   work_dir, log_filepath=log_filepath)
    check_call_log(['7z', 'x', '-y', os.path.join(work_dir, 'qt-creator_build', 'qtcreator_dev.7z'), '-o' + qtcreator_dev_path],
                   work_dir, log_filepath=log_filepath)
    build_qtcreator_plugins(additional_plugins, qtcreator_path, qtcreator_dev_path, icu_url=icu_local_url,
                            openssl_url=openssl_local_url, log_filepath=log_filepath, use_cmake=use_cmake)

    qtcreator_sha = bld_utils.get_commit_SHA(qtcreator_source)
    with open(os.path.join(work_dir, 'QTC_SHA1'), 'w') as f:
        f.write(qtcreator_sha + '\n')

    if bldinstallercommon.is_linux_platform():
        # summary of git SHA1s
        sha1s = collect_qt_creator_plugin_sha1s(additional_plugins)
        licensemanaging_source = os.path.join(work_dir, 'license-managing')
        if os.path.exists(licensemanaging_source):
            sha1s.append('license-managing: ' + bld_utils.get_commit_SHA(licensemanaging_source))
        sha1s.append('qt-creator: ' + qtcreator_sha)
        with open(os.path.join(work_dir, 'SHA1'), 'w') as f:
            f.writelines([sha + '\n' for sha in sha1s])

    # Create opensource source package
    create_qtcreator_source_package(os.path.join(work_dir, 'qt-creator'), None, qtcreator_version,
                                    'opensource', work_dir, log_filepath)
    # Create enterprise source package
    if installer_patch:
        check_call_log(['git', 'apply', '-3', installer_patch],
                       os.path.join(work_dir, 'qt-creator'), log_filepath=log_filepath)
        create_qtcreator_source_package(os.path.join(work_dir, 'qt-creator'), None, qtcreator_version,
                                        'enterprise', work_dir, log_filepath)

    # Build sdktool
    if sdktool_qtbase_src:
        sdktool_build_path = os.path.join(work_dir, 'sdktool_build')
        sdktool_target_path = os.path.join(sdktool_build_path, 'target')
        with BuildLog(log_filepath) as f:
            bld_sdktool.build_sdktool(sdktool_qtbase_src, os.path.join(sdktool_build_path, 'qt'),
                                      os.path.join(work_dir, 'qt-creator', 'src', 'tools', 'sdktool'),
                                      os.path.join(sdktool_build_path, 'src', 'tools', 'sdktool'),
                                      sdktool_target_path,
                                      'nmake' if bldinstallercommon.is_win_platform() else 'make',
                                      use_cmake=use_cmake,
                                      redirect_output=f)
            bld_sdktool.zip_sdktool(sdktool_target_path, os.path.join(work_dir, 'sdktool.7z'),
                                    redirect_output=f)

    # notarize
    if bldinstallercommon.is_mac_platform() and notarize:
        notarizeDmg(os.path.join(work_dir, 'qt-creator_build', 'qt-creator.dmg'), 'Qt Creator')

    # Upload
    file_upload_list = [] # pairs (source, dest), source relative to WORK_DIR, dest relative to server + dir_path
    snapshot_upload_list = [] # pairs (source, dest), source relative to server + dir_path, dest relative to snapshot server + snapshot_path

    # build log
    file_upload_list.append((log_filepath, target_env_dir + '/build_log.txt'))

    # macOS opensource dmg
    if bldinstallercommon.is_mac_platform() and not skip_dmg:
        dmg_filename = 'qt-creator-opensource-mac-x86_64-' + qtcreator_version + '.dmg'
        file_upload_list.append(('qt-creator_build/qt-creator.dmg', dmg_filename))
        snapshot_upload_list.append((dmg_filename, dmg_filename))

    # source packages
    source_package_list = glob(os.path.join(work_dir, 'qt-creator-*-src-' + qtcreator_version + '.*'))
    file_upload_list.extend([(os.path.basename(fn), '') for fn in source_package_list])
    qtc_source_packages = [os.path.basename(fn) for fn in
                           glob(os.path.join(work_dir, 'qt-creator-opensource-src-' + qtcreator_version + '.*'))]
    snapshot_upload_list.extend([(fn, fn) for fn in qtc_source_packages])

    if bldinstallercommon.is_linux_platform():
        # summary of git SHA1s
        file_upload_list.append(('SHA1', ''))
    file_upload_list.append(('QTC_SHA1', target_env_dir + '/QTC_SHA1'))
    snapshot_upload_list.append((target_env_dir + '/QTC_SHA1', target_env_dir + '/SHA1'))

    # installer 7z sources
    file_upload_list.append(('qt-creator_build/qtcreator.7z', target_env_dir + '/qtcreator.7z'))
    file_upload_list.append(('qt-creator_build/qtcreator_dev.7z', target_env_dir + '/qtcreator_dev.7z'))
    snapshot_upload_list.append((target_env_dir + '/qtcreator.7z', target_env_dir + '/qtcreator.7z'))
    snapshot_upload_list.append((target_env_dir + '/qtcreator_dev.7z', target_env_dir + '/qtcreator_dev.7z'))
    file_upload_list.append(('qt-creator_build/qtcreator-debug.7z', target_env_dir + '/qtcreator-debug.7z'))
    snapshot_upload_list.append((target_env_dir + '/qtcreator-debug.7z', target_env_dir + '/qtcreator-debug.7z'))
    for plugin in additional_plugins:
        plugin_name = plugin.name + '.7z'
        plugin_dev_name = plugin.name + '_dev.7z'
        plugin_debug_name = plugin.name + '-debug.7z'
        if os.path.isfile(os.path.join(work_dir, plugin_name)):
            file_upload_list.append((plugin_name, target_env_dir + '/' + plugin_name))
        if os.path.isfile(os.path.join(work_dir, plugin_dev_name)):
            file_upload_list.append((plugin_dev_name, target_env_dir + '/' + plugin_dev_name))
        if os.path.isfile(os.path.join(work_dir, plugin_debug_name)):
            file_upload_list.append((plugin_debug_name, target_env_dir + '/' + plugin_debug_name))
    if bldinstallercommon.is_win_platform():
        if not skip_cdb:
            file_upload_list.append(('qt-creator_build/qtcreatorcdbext.7z', target_env_dir + '/qtcreatorcdbext.7z'))
            snapshot_upload_list.append((target_env_dir + '/qtcreatorcdbext.7z', target_env_dir + '/qtcreatorcdbext.7z'))
            file_upload_list.append(('qt-creator_build/wininterrupt.7z', target_env_dir + '/wininterrupt.7z'))
            snapshot_upload_list.append((target_env_dir + '/wininterrupt.7z', target_env_dir + '/wininterrupt.7z'))
    if sdktool_qtbase_src:
        file_upload_list.append(('sdktool.7z', target_env_dir + '/sdktool.7z'))
    # upload files
    upload_files(base_path, file_upload_list, optionDict)
    remote_path = base_path + '/latest'
    # public snapshot
    if snapshot_server and snapshot_path:
        qtcreator_shortversion = re.match(r'\d+[.]\d+', qtcreator_version).group()
        snapshot_base = snapshot_path + '/' + qtcreator_shortversion + '/' + qtcreator_version + '/installer_source/'
        snapshot_target = snapshot_base + build_id + '/'
        bldinstallercommon.do_execute_sub_process(
            [optionDict['SSH_COMMAND'], pkg_storage_server,
             "ssh", snapshot_server,
             'mkdir', '-p', snapshot_target + target_env_dir],
            work_dir, True)
        bldinstallercommon.do_execute_sub_process(
            [optionDict['SSH_COMMAND'], pkg_storage_server,
             "ssh", snapshot_server,
             'ln', '-sfn', snapshot_target, snapshot_base + 'latest'],
            work_dir, True)

        for source, destination in snapshot_upload_list:
            cmd_args = [optionDict['SSH_COMMAND'], pkg_storage_server, "scp",
                        remote_path + '/' + source,
                        snapshot_server + ':' + snapshot_target + '/' + destination]
            bldinstallercommon.do_execute_sub_process(cmd_args, work_dir)
    # create link from job name to display name
    update_job_link(unversioned_base_path, base_path, optionDict)

###############################
# handle_sdktool_build
###############################
def handle_sdktool_build(optionDict):
    # environment
    target_env_dir = optionDict['QTC_PLATFORM']
    work_dir = optionDict['WORK_DIR']
    qtcreator_version = get_qtcreator_version(os.path.join(work_dir, 'qt-creator'), optionDict)
    qtcreator_edition_name = optionDict.get('QT_CREATOR_EDITION_NAME') # optional
    base_path = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/qtcreator/snapshots/' + qtcreator_version
    sdktool_qtbase_src = optionDict['SDKTOOL_QTBASESRC_BASE'] + optionDict['SDKTOOL_QTBASESRC_EXT']
    # build
    qtcreator_src = os.path.join(work_dir, 'qt-creator')
    sdktool_build_path = os.path.join(work_dir, 'sdktool_build')
    sdktool_target_path = os.path.join(sdktool_build_path, 'target')
    bld_sdktool.build_sdktool(sdktool_qtbase_src, os.path.join(sdktool_build_path, 'qt'),
                              os.path.join(work_dir, 'qt-creator', 'src', 'tools', 'sdktool'),
                              os.path.join(sdktool_build_path, 'src', 'tools', 'sdktool'),
                              sdktool_target_path,
                              'nmake' if bldinstallercommon.is_win_platform() else 'make',
                              use_cmake=True)
    bld_sdktool.zip_sdktool(sdktool_target_path, os.path.join(work_dir, 'sdktool.7z'))
    file_upload_list = [('sdktool.7z', target_env_dir + '/sdktool.7z')]
    if bldinstallercommon.is_win_platform(): # wininterrupt & qtcreatorcdbext
        check_call_log(['python', '-u', os.path.join(qtcreator_src, 'scripts', 'build.py'),
                        '--src', qtcreator_src,
                        '--build', os.path.join(work_dir, 'build'),
                        '--no-qtcreator'],
                       work_dir)
        file_upload_list += [('build/wininterrupt.7z', target_env_dir + '/wininterrupt.7z'),
                             ('build/qtcreatorcdbext.7z', target_env_dir + '/qtcreatorcdbext.7z')]
    # upload
    if qtcreator_edition_name:
        base_path += '_' + qtcreator_edition_name
    upload_files(base_path, file_upload_list, optionDict)

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
    batch_process_installer_bld.handle_installer_build(optionDict, installer_type, 'release', arch)
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
        sign_installer(installer_output_dir, installer_name, installer_name_base, optionDict.get('NOTARIZE'))
        # copy installer(s) to respective build number directory:
        remote_copy_installer(optionDict, remote_path_snapshot, installer_name, installer_output_dir, installer_name_final)
        # Keep only the latest one in the "latest_available" directory i.e. delete the previous one
        replace_latest_successful_installer(optionDict, installer_name, installer_name_final, remote_path_latest_available, installer_output_dir)
    # Trigger rta cases
    trigger_rta(optionDict, os.path.join(SCRIPT_ROOT_DIR, pkg_constants.RTA_DESCRIPTION_FILE_DIR_NAME))
    # Do we upload the installers to opensource/public network drive?
    if optionDict.get('EXPORT_OPENSOURCE_INSTALLER', '').lower() in ["yes", "true", "1"]:
        # opensource distribution server address and path
        ext_server_base_url  = optionDict['EXT_SERVER_BASE_URL']
        ext_server_base_path = optionDict['EXT_SERVER_BASE_PATH']
        # opensource distribution server directories  #TODO, below
        short_version = '.'.join(optionDict['VERSION'].split('.')[:2])  # 4.10.x -> 4.10
        ext_dest_dir = ext_server_base_path + '/snapshots/' + project_name + '/' + short_version + '/' + optionDict['VERSION_FULL'] + '/' + optionDict['BUILD_NUMBER']
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
        regex = re.compile('.*' + optionDict['VERSION'])
    installer_base_name = "".join(regex.findall(installer_name_final))
    if not installer_base_name:
        print('*** Empty installer base name string')
    else:
        old_installer = ls_installer_dir + '/' + installer_base_name + '*'
        # delete old installer
        cmd_args = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR'], 'rm', '-f', old_installer]
        bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR, False)
        # save new installer to latest_successful directory
        remote_copy_installer(optionDict, ls_installer_dir, installer_name, installer_output, installer_name_final)


###############################
# Sign installer
###############################
def sign_installer(installer_output_dir, installer_name, installer_name_base, notarize):
    if installer_name.endswith(".dmg"):
        sign_mac_executable(installer_name_base + '.app', installer_output_dir, True)
        disk_image_filepath = os.path.join(installer_output_dir, installer_name_base) + '.dmg'
        cmd_args = ['hdiutil', 'create', '-srcfolder', os.path.join(installer_output_dir, installer_name_base) + '.app', '-volname', installer_name_base, '-format', 'UDBZ', disk_image_filepath, '-ov', '-scrub', '-size', '4g']
        bldinstallercommon.do_execute_sub_process(cmd_args, installer_output_dir)
        if notarize:
            notarizeDmg(disk_image_filepath, installer_name_base)
    if installer_name.endswith(".exe"):
        sign_windows_executable(installer_name, installer_output_dir, True)


def notarizeDmg(dmgPath, installer_name_base):
    # bundle-id is just a unique identifier without any special meaning, used to track the notarization progress
    bundleId = installer_name_base + "-" + strftime('%Y-%m-%d', gmtime())
    bundleId = bundleId.replace('_', '-').replace(' ', '')  # replace illegal characters for bundleId
    args = ['python3', 'notarize.py', '--dmg=' + dmgPath, '--bundle-id=' + bundleId]
    bldinstallercommon.do_execute_sub_process(args, SCRIPT_ROOT_DIR)


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
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)


###############################
# Delete remote directory tree
###############################
def delete_remote_directory_tree(optionDict, remote_dir):
    cmd_args = [optionDict['SSH_COMMAND'], optionDict['PACKAGE_STORAGE_SERVER_ADDR'], 'rm -rf', remote_dir]
    bldinstallercommon.do_execute_sub_process(cmd_args, optionDict['WORK_DIR'])


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
                    url = rta_server_base_url + item + '/build?token=RTA_JENKINS'
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
    batch_process_installer_bld.handle_repo_build(optionDict, 'release', arch, update_staging_repo, update_production_repo)
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


###############################
# initialize_extra_module_build
###############################
def initialize_extra_module_build_src(optionDict):
    # initialize extra module src, doc and example directory hierarchy
    module_name = optionDict['MODULE_NAME']
    module_version = optionDict['MODULE_VERSION']
    remote_dest_dir_base = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/' + module_name + '/' + module_version
    remote_dest_dir = remote_dest_dir_base + '/' + optionDict['BUILD_NUMBER']
    remote_dest_dir_latest = remote_dest_dir_base + '/' + 'latest'
    remote_directories = []
    for dir_name in ['src', 'doc', 'examples']:
        remote_directories.append(remote_dest_dir + '/' + dir_name)
    initialize_extra_module_build(optionDict, remote_directories, remote_dest_dir, remote_dest_dir_latest)


######################################
# initialize_extra_module_build
######################################
def initialize_extra_module_build(optionDict, remote_directories, remote_snapshot_base_dir, remote_snapshot_latest_link):
    for remote_dir in remote_directories:
        create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_dir)
    # Update latest link
    update_latest_link(optionDict, remote_snapshot_base_dir, remote_snapshot_latest_link)


###############################
# build_extra_module_src_pkg
###############################
def build_extra_module_src_pkg(optionDict):
    import build_doc
    module_name = optionDict['MODULE_NAME']
    module_version = optionDict['MODULE_VERSION']
    module_sha1 = optionDict.get('MODULE_SHA1', '')
    module_git_repo = optionDict['GIT_MODULE_REPO']
    module_git_repo_branch = optionDict['GIT_MODULE_REPO_BRANCH']
    skip_syncqt = optionDict.get('SKIP_SYNCQT', '')
    latest_extra_module_dir = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/' + module_name + '/' + module_version + '/' + 'latest'
    script_dir = os.path.dirname(os.path.realpath(__file__))
    #create dir
    module_dir = os.path.join(optionDict['WORK_DIR'], module_name)
    bldinstallercommon.create_dirs(module_dir)
    #clone repo
    shutil.rmtree(module_dir, ignore_errors=True)
    bldinstallercommon.clone_repository(module_git_repo, module_git_repo_branch, module_dir, True)
    if module_sha1:
        cmd_args = ['git', 'checkout', module_sha1]
        bldinstallercommon.do_execute_sub_process(cmd_args, module_dir)
    #make src package
    cmd_args = [os.path.join(script_dir, 'mksrc.sh'), '-v', module_version, '-l', optionDict['LICENSE'], '--single-module']
    if skip_syncqt:
        cmd_args += ['--skip-syncqt']
    bldinstallercommon.do_execute_sub_process(cmd_args, module_dir)
    #extract examples
    cmd_args = [os.path.join(script_dir, 'extract_examples.sh'), '-n', module_name, '-l', optionDict['LICENSE'], '-v', module_version, '-u', optionDict['PACKAGE_STORAGE_SERVER_USER'], '-s', optionDict['PACKAGE_STORAGE_SERVER'], '-d', optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'], '-b', optionDict['BUILD_NUMBER']]
    bldinstallercommon.do_execute_sub_process(cmd_args, module_dir)
    #Copy src package to the server
    extra_module_src_dir = optionDict['PACKAGE_STORAGE_SERVER_USER'] + '@' + optionDict['PACKAGE_STORAGE_SERVER'] + ':' + optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + optionDict['LICENSE'] + '/' + module_name + '/' + module_version
    src_pkg = False
    file_list = os.listdir(module_dir)
    for file_name in file_list:
        if file_name.startswith(module_name + '-' + optionDict['LICENSE'] + '-src-' + module_version):
            src_pkg = True
            cmd_args = ['scp', file_name, extra_module_src_dir + '/latest/src']
            bldinstallercommon.do_execute_sub_process(cmd_args, module_dir)
    # handle doc package creation, this may fail as some extra modules are missing docs
    try:
        build_doc.handle_module_doc_build(optionDict)
        # copy archived doc files to network drive if exists, we use Linux only to generate doc archives
        local_docs_dir = os.path.join(SCRIPT_ROOT_DIR, 'doc_archives')
        if os.path.exists(local_docs_dir):
            # create remote doc dir
            doc_target_dir = optionDict['PACKAGE_STORAGE_SERVER_ADDR'] + ':' + latest_extra_module_dir + '/' + 'doc'
            remote_copy_archives(doc_target_dir, local_docs_dir)
    except Exception:
        print('Failed to build doc package for: {0}'.format(module_git_repo))
        pass

    # if we got here, we have at least the src packages, update symlink latest_successful -> latest
    if src_pkg:
        latest_successful_dir = latest_extra_module_dir + '_successful'
        update_latest_link(optionDict, latest_extra_module_dir, latest_successful_dir)


###############################
# git archive given repository
###############################
def git_archive_repo(optionDict, repo_and_ref):
    archive_name = bldinstallercommon.git_archive_repo(repo_and_ref)
    # Create remote dest directories
    remote_dest_dir_base = optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] + '/' + project_name + '/' + ref
    remote_dest_dir = remote_dest_dir_base + '/' + optionDict['BUILD_NUMBER']
    remote_dest_dir_latest = remote_dest_dir_base + '/' + 'latest'
    create_remote_dirs(optionDict, optionDict['PACKAGE_STORAGE_SERVER_ADDR'], remote_dest_dir)
    update_latest_link(optionDict, remote_dest_dir, remote_dest_dir_latest)
    # upload archive to network disk
    dest_dir = optionDict['PACKAGE_STORAGE_SERVER_USER'] + '@' + optionDict['PACKAGE_STORAGE_SERVER'] + ':' + remote_dest_dir
    cmd_args = ['scp', archive_name, remote_dest_dir]
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR)


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
        optionDict['TARGET_ENV'] = args.target_env if args.target_env else getDefaultTargetEnv()
        optionDict['BUILD_NUMBER'] = str(strftime('%Y%m%d%H%M%S', gmtime()))
        optionDict['PACKAGE_STORAGE_SERVER_ADDR'] = optionDict['PACKAGE_STORAGE_SERVER_USER'] + '@' + optionDict['PACKAGE_STORAGE_SERVER']
        (ifw_dest_dir_name, ifw_dest_dir) = get_ifw_remote_destination_dir(optionDict)
        optionDict['IFW_TOOLS_BASE_URL'] = ifw_dest_dir
    else:
        optionDict = dict(os.environ)
        # Check for command line overrides
        optionDict['LICENSE'] = args.license
        optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] = args.path
        optionDict['OPENSSL_LIBS'] = args.openssl_libs
        optionDict['SNAPSHOT_SERVER_PATH'] = args.snapshot_path
        optionDict['TARGET_ENV'] = args.target_env if args.target_env else os.environ.get('cfg')
        optionDict['BUILD_NUMBER'] = args.build_number if args.build_number else os.environ.get('BUILD_NUMBER')

        optionDict['SIGNING_SERVER'] = get_pkg_value("SIGNING_SERVER")
        optionDict['SIGNING_PASSWORD'] = get_pkg_value("SIGNING_PASSWORD")
        optionDict['USP_SERVER_URL'] = get_pkg_value("USP_SERVER_URL")
        optionDict['USP_AUTH_KEY'] = get_pkg_value("USP_AUTH_KEY")
        optionDict['PACKAGE_STORAGE_SERVER_USER'] = get_pkg_value("PACKAGE_STORAGE_SERVER_USER")
        optionDict['PACKAGE_STORAGE_SERVER'] = get_pkg_value("PACKAGE_STORAGE_SERVER")
        optionDict['PACKAGE_STORAGE_SERVER_ADDR'] = args.server or get_pkg_value("PACKAGE_STORAGE_SERVER_ADDR")
        optionDict['PACKAGE_STORAGE_SERVER_PATH_HTTP'] = args.override_server_path_http or get_pkg_value("PACKAGE_STORAGE_SERVER_PATH_HTTP")
        optionDict['SNAPSHOT_SERVER'] = args.snapshot_server or get_pkg_value("SNAPSHOT_SERVER")
        optionDict['EXT_SERVER_BASE_URL'] = get_pkg_value("EXT_SERVER_BASE_URL")
        optionDict['RTA_SERVER_BASE_URL'] = get_pkg_value("RTA_SERVER_BASE_URL")
        optionDict['PKG_STAGING_SERVER'] = get_pkg_value("PKG_STAGING_SERVER")
        optionDict['PKG_STAGING_SERVER_UNAME'] = get_pkg_value("PKG_STAGING_SERVER_UNAME")
        optionDict['PROD_USER'] = get_pkg_value("PROD_USER")
        optionDict['PROD_ADDR'] = get_pkg_value("PROD_ADDR")

        if LOCAL_MODE:
            from getpass import getuser
            optionDict['PACKAGE_STORAGE_SERVER_USER'] = getuser() # current user
            optionDict['PACKAGE_STORAGE_SERVER'] = "127.0.0.1"
            optionDict['PACKAGE_STORAGE_SERVER_ADDR'] = optionDict['PACKAGE_STORAGE_SERVER_USER'] + "@" + optionDict['PACKAGE_STORAGE_SERVER']
            optionDict['PACKAGE_STORAGE_SERVER_BASE_DIR'] = LOCAL_INSTALLER_DIR
            optionDict['SNAPSHOT_SERVER'] = optionDict['PACKAGE_STORAGE_SERVER_ADDR']
            optionDict['SNAPSHOT_SERVER_PATH'] = LOCAL_INSTALLER_DIR

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
    optionDict['WORK_DIR'] = WORK_DIR

    # Parse version number info if this build utilizes release description file
    if 'RELEASE_DESCRIPTION_FILE' in optionDict:
        # Path relative to script or absolute file path
        path = optionDict['RELEASE_DESCRIPTION_FILE']
        path = path if (os.path.isabs(path) and os.path.isfile(path)) else os.path.join(optionDict['WORK_DIR'], path)
        optionDict['RELEASE_DESCRIPTION_FILE'] = path
        # resolve configuration files base dir
        confBaseDir = optionDict['CONFIGURATIONS_FILE_BASE_DIR']
        confBaseDir = confBaseDir if (os.path.isabs(confBaseDir) and os.path.isdir(confBaseDir)) else os.path.join(optionDict['WORK_DIR'], confBaseDir)
        optionDict['CONFIGURATIONS_FILE_BASE_DIR'] = confBaseDir

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
    # Define supported build steps
    bld_ifw                                 = 'ifw'
    bld_qtcreator                           = 'build_creator'
    bld_qtcreator_plugins                   = 'build_qtcreator_plugins'
    bld_qtc_sdktool                         = 'build_sdktool'
    bld_gammaray                            = 'build_gammaray'
    create_online_repository                = 'repo_build'
    create_offline_installer                = 'offline_installer'
    create_online_installer                 = 'online_installer'
    bld_icu_init                            = 'init_icu_bld'
    bld_icu                                 = 'icu_bld'
    bld_licheck                             = 'licheck_bld'
    init_extra_module_build_cycle_src       = 'init_app_src'
    execute_extra_module_build_cycle_src    = 'build_qt5_app_src'
    archive_repository                      = 'archive_repo'
    CMD_LIST =  (bld_ifw, bld_qtcreator, bld_qtcreator_plugins, bld_qtc_sdktool, bld_gammaray, bld_icu_init, bld_icu, bld_licheck)
    CMD_LIST += (create_online_installer, create_online_repository, create_offline_installer)
    CMD_LIST += (init_extra_module_build_cycle_src, execute_extra_module_build_cycle_src, archive_repository)

    parser = argparse.ArgumentParser(prog="Build Wrapper", description="Manage all packaging related build steps.")
    parser.add_argument("-c", "--command", dest="command", required=True, choices=CMD_LIST, help=CMD_LIST)
    parser.add_argument("--pkg-conf-file", dest="pkg_conf_file", default="", help="instead of reading various config options from env variables read them from the given file.")
    parser.add_argument("-l", "--license", dest="license", default="", help="license type: enterprise or opensource")
    parser.add_argument("-b", "--build_number", dest="build_number", default="", help="Unique build number identifier")
    parser.add_argument("-s", "--server", dest="server", default="", help="Upload server e.g. <user>@<host>")
    parser.add_argument("--override_server_path_http", dest="override_server_path_http", help="In case we already have local downloaded modules or we want to use a different source it can be overwritten here.")
    parser.add_argument("-p", "--path", dest="path", default="", help="Path on server")
    parser.add_argument("-e", "--target_env", dest="target_env", default="", help="Target environment: Linux, Linux_64, mac, win")
    parser.add_argument("-o", "--openssl_libs", dest="openssl_libs", default="", help="Url for pre-compiled openssl libraries")
    parser.add_argument("--archive-repo", dest="archive_repo", default="", help="Create Git archive from the given repository. Use syntax: \"git-url#ref\"")
    parser.add_argument("--snapshot-server", dest="snapshot_server", default="", help="Additional snapshot upload server <user>@<host> (is uploaded from upload server)")
    parser.add_argument("-snapshot-path", dest="snapshot_path", default="", help="Path on additional snapshot upload server")
    parser.add_argument("--qtcreator-plugin-config", help="Path to Qt Creator plugin specification to be used with build_qtcreator_plugins command")
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
    # Qt Creator 3rdparty plugins
    elif args.command == bld_qtcreator_plugins:
        handle_qt_creator_plugins_build(optionDict, args.qtcreator_plugin_config)
    # sdktool
    elif args.command == bld_qtc_sdktool:
        handle_sdktool_build(optionDict)
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
    elif args.command == init_extra_module_build_cycle_src:
        initialize_extra_module_build_src(optionDict)
    elif args.command == execute_extra_module_build_cycle_src:
        build_extra_module_src_pkg(optionDict)
    elif args.command == archive_repository:
        git_archive_repo(optionDict, args.archive_repo)
    else:
        print('Unsupported command')
