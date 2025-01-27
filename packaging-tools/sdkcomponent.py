#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#############################################################################
#
# Copyright (C) 2022 The Qt Company Ltd.
# Contact: https://www.qt.io/licensing/
#
# This file is part of the release tools of the Qt Toolkit.
#
# $QT_BEGIN_LICENSE:GPL-EXCEPT$
# Commercial License Usage
# Licensees holding valid commercial Qt licenses may use this file in
# accordance with the commercial license agreement provided with the
# Software or, alternatively, in accordance with the terms contained in
# a written agreement between you and The Qt Company. For licensing terms
# and conditions see https://www.qt.io/terms-conditions. For further
# information use the contact form at https://www.qt.io/contact-us.
#
# GNU General Public License Usage
# Alternatively, this file may be used under the terms of the GNU
# General Public License version 3 as published by the Free Software
# Foundation with exceptions as appearing in the file LICENSE.GPL3-EXCEPT
# included in the packaging of this file. Please review the following
# information to ensure the GNU General Public License requirements will
# be met: https://www.gnu.org/licenses/gpl-3.0.html.
#
# $QT_END_LICENSE$
#
#############################################################################

import ntpath
import os
from configparser import ConfigParser
from typing import Any, List

from archiveresolver import ArchiveLocationResolver
from bldinstallercommon import config_section_map, is_content_url_valid, safe_config_key_fetch
from logging_util import init_logger

log = init_logger(__name__, debug_mode=False)

ONLINE_ARCHIVE_LIST_TAG = '<!--ONLINE_ARCHIVE_LIST-->'


class SdkComponent:
    """SdkComponent class contains all required info for one installable SDK component"""
    class DownloadableArchive:
        """DownloadableArchive subclass contains all required info about data packages for one SDK component"""
        def __init__(
            self,
            archive: str,
            package_name: str,
            parent_target_install_base: str,
            archive_server_name: str,
            target_config: ConfigParser,
            archive_location_resolver: ArchiveLocationResolver,
            key_value_substitution_list: List[str],
        ) -> None:
            self.archive_uri = config_section_map(target_config, archive)['archive_uri']
            self.archive_action = safe_config_key_fetch(target_config, archive, 'archive_action')
            self.extract_archive = safe_config_key_fetch(target_config, archive, 'extract_archive')
            self.package_strip_dirs = safe_config_key_fetch(target_config, archive, 'package_strip_dirs')
            self.package_finalize_items = safe_config_key_fetch(target_config, archive, 'package_finalize_items')
            # parent's 'target_install_base'
            self.parent_target_install_base = parent_target_install_base
            # in case the individual archive needs to be installed outside the root dir specified by the parent component
            self.target_install_base: str = safe_config_key_fetch(target_config, archive, 'target_install_base')
            # this is relative to 1) current archive's 'target_install_base' 2) parent components 'target_install_base'. (1) takes priority
            self.target_install_dir: str = safe_config_key_fetch(target_config, archive, 'target_install_dir').lstrip(os.path.sep)
            self.rpath_target = safe_config_key_fetch(target_config, archive, 'rpath_target')
            self.component_sha1_file = safe_config_key_fetch(target_config, archive, 'component_sha1_file')
            self.nomalize_archive_uri(package_name, archive_server_name, archive_location_resolver)
            self.archive_name = safe_config_key_fetch(target_config, archive, 'archive_name')
            if not self.archive_name:
                self.archive_name = self.path_leaf(self.archive_uri)
                # Parse unnecessary extensions away from filename (QTBUG-39219)
                known_archive_types = ['.tar.gz', '.tar', '.zip', '.tar.xz', '.tar.bz2']
                for item in known_archive_types:
                    if self.archive_name.endswith(item):
                        self.archive_name = self.archive_name.replace(item, '')
                if not self.archive_name.endswith('.7z'):
                    self.archive_name += '.7z'
            # substitute key-value pairs if any
            for item in key_value_substitution_list:
                self.target_install_base = self.target_install_base.replace(item[0], item[1])
                self.target_install_dir = self.target_install_dir.replace(item[0], item[1])
                self.archive_name = self.archive_name.replace(item[0], item[1])

        def nomalize_archive_uri(
            self, package_name: str, archive_server_name: str, archive_location_resolver: ArchiveLocationResolver
        ) -> None:
            self.archive_uri = archive_location_resolver.resolve_full_uri(package_name, archive_server_name, self.archive_uri)

        def check_archive_data(self) -> Any:
            if self.archive_uri.startswith('http'):
                res = is_content_url_valid(self.archive_uri)
                if not res:
                    return '*** Archive check fail! ***\n*** Unable to locate archive: ' + self.archive_uri
            elif not os.path.isfile(self.archive_uri):
                return '*** Archive check fail! ***\n*** Unable to locate archive: ' + self.archive_uri
            return None

        def path_leaf(self, path: str) -> str:
            head, tail = ntpath.split(path)
            return tail or ntpath.basename(head)

        def get_archive_installation_directory(self) -> str:
            if self.target_install_base:
                return self.target_install_base + os.path.sep + self.target_install_dir
            return self.parent_target_install_base + os.path.sep + self.target_install_dir

    def __init__(
        self,
        section_name: str,
        target_config: ConfigParser,
        packages_full_path_list: List[str],
        archive_location_resolver: ArchiveLocationResolver,
        key_value_substitution_list: List[str],
    ):
        self.static_component = safe_config_key_fetch(target_config, section_name, 'static_component')
        self.root_component = safe_config_key_fetch(target_config, section_name, 'root_component')
        self.package_name = section_name
        self.package_subst_name = section_name
        self.packages_full_path_list = packages_full_path_list
        self.archives = safe_config_key_fetch(target_config, section_name, 'archives')
        self.archives = self.archives.replace(' ', '').replace('\n', '')
        self.archives_extract_dir = safe_config_key_fetch(target_config, section_name, 'archives_extract_dir')
        self.archive_server_name = safe_config_key_fetch(target_config, section_name, 'archive_server_name')
        self.downloadable_archive_list: List[SdkComponent.DownloadableArchive] = []  # pylint: disable=E0601
        self.target_install_base = safe_config_key_fetch(target_config, section_name, 'target_install_base')
        self.version = safe_config_key_fetch(target_config, section_name, 'version')
        self.version_tag = safe_config_key_fetch(target_config, section_name, 'version_tag')
        self.package_default = safe_config_key_fetch(target_config, section_name, 'package_default')
        self.install_priority = safe_config_key_fetch(target_config, section_name, 'install_priority')
        self.sorting_priority = safe_config_key_fetch(target_config, section_name, 'sorting_priority')
        self.component_sha1 = ""
        self.component_sha1_uri = safe_config_key_fetch(target_config, section_name, 'component_sha1_uri')
        if self.component_sha1_uri:
            self.component_sha1_uri = archive_location_resolver.resolve_full_uri(self.package_name, self.archive_server_name, self.component_sha1_uri)
        self.key_value_substitution_list = key_value_substitution_list
        self.archive_skip = False
        self.include_filter = safe_config_key_fetch(target_config, section_name, 'include_filter')
        self.downloadable_arch_list_qs: List[Any] = []
        self.pkg_template_dir = ''
        self.sanity_check_error_msg = ''
        self.target_config = target_config
        self.archive_location_resolver = archive_location_resolver
        self.meta_dir_dest: str = ""
        self.temp_data_dir: str = ""
        # substitute key-value pairs if any
        for item in self.key_value_substitution_list:
            self.target_install_base = self.target_install_base.replace(item[0], item[1])
            self.version = self.version.replace(item[0], item[1])

    def is_root_component(self) -> bool:
        if self.root_component in ('yes', 'true'):
            return True
        return False

    def set_archive_skip(self, do_skip: bool) -> None:
        self.archive_skip = do_skip

    def validate(self) -> None:
        # look up correct package template directory from list
        found = False
        for item in self.key_value_substitution_list:
            self.package_name = self.package_name.replace(item[0], item[1])
        for item in self.packages_full_path_list:
            template_full_path = os.path.normpath(item + os.sep + self.package_subst_name)
            if os.path.exists(template_full_path):
                if not found:
                    # take the first match
                    self.pkg_template_dir = template_full_path
                    found = True
                else:
                    # sanity check, duplicate template should not exist to avoid
                    # problems!
                    log.warning("Found duplicate template for: %s", self.package_name)
                    log.warning("Ignoring: %s", template_full_path)
                    log.warning("Using:    %s", self.pkg_template_dir)
        self.parse_archives(self.target_config, self.archive_location_resolver)
        self.check_component_data()

    def check_component_data(self) -> None:
        if self.static_component:
            if not os.path.isfile(self.static_component):
                self.sanity_check_fail(self.package_name, 'Unable to locate given static package: ' + self.static_component)
                return
            # no more checks needed for static component
            return
        if not self.package_name:
            self.sanity_check_fail(self.package_name, 'Undefined package name?')
            return
        if self.archives and not self.target_install_base:
            self.sanity_check_fail(self.package_name, 'Undefined target_install_base?')
            return
        if self.version and not self.version_tag:
            self.sanity_check_fail(self.package_name, 'Undefined version_tag?')
            return
        if self.version_tag and not self.version:
            self.sanity_check_fail(self.package_name, 'Undefined version?')
            return
        if self.package_default not in ['true', 'false', 'script']:
            self.package_default = 'false'
        # check that package template exists
        if not os.path.exists(self.pkg_template_dir):
            self.sanity_check_fail(self.package_name, 'Package template dir does not exist: ' + self.pkg_template_dir)
            return
        if not self.archive_skip:
            # next check that archive locations exist
            for archive in self.downloadable_archive_list:
                error_msg = archive.check_archive_data()
                if error_msg:
                    self.sanity_check_fail(self.package_name, error_msg)
                    return

    def sanity_check_fail(self, component_name: str, message: str) -> None:
        self.sanity_check_error_msg = '*** Sanity check fail! ***\n*** Component: [' + component_name + ']\n*** ' + message

    def is_valid(self) -> bool:
        if self.sanity_check_error_msg:
            return False
        return True

    def error_msg(self) -> str:
        return self.sanity_check_error_msg

    def parse_archives(self, target_config: ConfigParser, archive_location_resolver: ArchiveLocationResolver) -> None:
        if self.archives:
            archives_list = self.archives.split(',')
            for archive in archives_list:
                if not archive:
                    log.warning("[%s]: Archive list in config has ',' issues", self.package_name)
                    continue
                # check that archive template exists
                if not target_config.has_section(archive):
                    raise RuntimeError(f'*** Error! Given archive section does not exist in configuration file: {archive}')
                archive_obj = SdkComponent.DownloadableArchive(archive, self.package_name, self.target_install_base, self.archive_server_name,
                                                               target_config, archive_location_resolver,
                                                               self.key_value_substitution_list)
                self.downloadable_archive_list.append(archive_obj)

    def generate_downloadable_archive_list(self) -> List[List[str]]:
        """Generate list that is embedded into package.xml"""
        output = ''
        for item in self.downloadable_archive_list:
            if not output:
                output = item.archive_name
            else:
                output = output + ', ' + item.archive_name

        temp_list = []
        temp_list.append([ONLINE_ARCHIVE_LIST_TAG, output])
        return temp_list

    def print_component_data(self) -> None:
        log.info("=============================================================")
        log.info("[%s]", self.package_name)
        if self.static_component:
            log.info("Static component: %s", self.static_component)
            return
        if self.root_component:
            log.info("Root component: %s", self.root_component)
        log.info("Include filter: %s", self.include_filter)
        log.info("Target install base: %s", self.target_install_base)
        log.info("Version: %s", self.version)
        log.info("Version tag: %s", self.version_tag)
        log.info("Package default: %s", self.package_default)
        if self.downloadable_archive_list:
            log.info(" Archives:")
            for archive in self.downloadable_archive_list:
                log.info("---------------------------------------------------------------")
                log.info("Downloadable archive name: %s", archive.archive_name)
                log.info("Strip dirs: %s", archive.package_strip_dirs)
                log.info("Target install dir: %s", archive.get_archive_installation_directory())
                log.info("RPath target: %s", archive.rpath_target)
                log.info("URI: %s", archive.archive_uri)
