# Author: echel0n <echel0n@sickrage.ca>
# URL: https://sickrage.ca
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import datetime
import os
import time
import traceback

import sickrage
from sickrage.core.blackandwhitelist import BlackAndWhiteList
from sickrage.core.common import WANTED
from sickrage.core.exceptions import CantRefreshShowException, \
    CantRemoveShowException, CantUpdateShowException, EpisodeDeletedException, \
    MultipleShowObjectsException
from sickrage.core.helpers import scrub, findCertainShow
from sickrage.core.queues import srQueue, srQueueItem, srQueuePriorities
from sickrage.core.scene_numbering import xem_refresh, get_xem_numbering_for_show
from sickrage.core.traktapi import srTraktAPI
from sickrage.core.tv.show import TVShow
from sickrage.indexers import srIndexerApi
from sickrage.indexers.exceptions import indexer_attributenotfound, \
    indexer_error, indexer_exception


class srShowQueue(srQueue):
    def __init__(self):
        srQueue.__init__(self, "SHOWQUEUE")

    @property
    def loadingShowList(self):
        return self._getLoadingShowList()

    def _isInQueue(self, show, actions):
        if not show:
            return False

        return show.indexerid in [x.show.indexerid if x.show else 0 for _, _, x in self.queue if x.action_id in actions]

    def _isBeing(self, show, actions):
        return self.currentItem is not None and show == self.currentItem.show and self.currentItem.action_id in actions

    def isInUpdateQueue(self, show):
        return self._isInQueue(show, (ShowQueueActions.UPDATE, ShowQueueActions.FORCEUPDATE))

    def isInRefreshQueue(self, show):
        return self._isInQueue(show, (ShowQueueActions.REFRESH,))

    def isInRenameQueue(self, show):
        return self._isInQueue(show, (ShowQueueActions.RENAME,))

    def isInSubtitleQueue(self, show):
        return self._isInQueue(show, (ShowQueueActions.SUBTITLE,))

    def isBeingAdded(self, show):
        return self._isBeing(show, (ShowQueueActions.ADD,))

    def isBeingUpdated(self, show):
        return self._isBeing(show, (ShowQueueActions.UPDATE, ShowQueueActions.FORCEUPDATE))

    def isBeingRefreshed(self, show):
        return self._isBeing(show, (ShowQueueActions.REFRESH,))

    def isBeingRenamed(self, show):
        return self._isBeing(show, (ShowQueueActions.RENAME,))

    def isBeingSubtitled(self, show):
        return self._isBeing(show, (ShowQueueActions.SUBTITLE,))

    def _getLoadingShowList(self):
        return [x for _, _, x in self.queue + [(0, 0, self.currentItem)] if x and x.isLoading]

    def updateShow(self, show, force=False):

        if self.isBeingAdded(show):
            raise CantUpdateShowException(
                str(show.name) + " is still being added, please wait until it is finished before trying to update.")

        if self.isBeingUpdated(show):
            raise CantUpdateShowException(
                str(show.name) + " is already being updated, can't update again until it's done.")

        if self.isInUpdateQueue(show):
            raise CantUpdateShowException(
                str(show.name) + " is in the process of being updated, can't update again until it's done.")

        return self.put(QueueItemForceUpdate(show)) if force else self.put(QueueItemUpdate(show))

    def refreshShow(self, show, force=False):
        if (self.isBeingRefreshed(show) or self.isInRefreshQueue(show)) and not force:
            raise CantRefreshShowException(
                "This show is already being refreshed or queued to be refreshed, skipping this request.")
        elif (self.isBeingUpdated(show) or self.isInUpdateQueue(show)) and not force:
            raise CantRefreshShowException(
                "A refresh was attempted but there is already an update queued or in progress. Since updates do a refresh at the end anyway I'm skipping this request.")

        sickrage.srCore.srLogger.debug("Queueing show refresh for {}".format(show.name))

        return self.put(QueueItemRefresh(show, force=force))

    def renameShowEpisodes(self, show):
        return self.put(QueueItemRename(show))

    def downloadSubtitles(self, show):
        return self.put(QueueItemSubtitle(show))

    def addShow(self, indexer, indexer_id, showDir, default_status=None, quality=None, flatten_folders=None,
                lang=None, subtitles=None, subtitles_sr_metadata=None, anime=None, scene=None, paused=None,
                blacklist=None, whitelist=None, default_status_after=None, archive=None):

        if lang is None:
            lang = sickrage.srCore.srConfig.INDEXER_DEFAULT_LANGUAGE

        return self.put(QueueItemAdd(indexer=indexer,
                                     indexer_id=indexer_id,
                                     showDir=showDir,
                                     default_status=default_status,
                                     quality=quality,
                                     flatten_folders=flatten_folders,
                                     lang=lang,
                                     subtitles=subtitles,
                                     subtitles_sr_metadata=subtitles_sr_metadata,
                                     anime=anime,
                                     scene=scene,
                                     paused=paused,
                                     blacklist=blacklist,
                                     whitelist=whitelist,
                                     default_status_after=default_status_after,
                                     archive=archive))

    def removeShow(self, show, full=False):
        if not show:
            raise CantRemoveShowException('Failed removing show: Show does not exist')
        elif not hasattr(show, 'indexerid'):
            raise CantRemoveShowException('Failed removing show: Show does not have an indexer id')
        elif self._isInQueue(show, (ShowQueueActions.REMOVE,)):
            raise CantRemoveShowException("{} is already queued to be removed".format(show))

        # remove other queued actions for this show.
        for _, _, x in self.queue:
            if x and x.show and x != self.currentItem and show.indexerid == x.show.indexerid:
                self.queue.remove(x)

        return self.put(QueueItemRemove(show=show, full=full))


class ShowQueueActions(object):
    def __init__(self):
        pass

    REFRESH = 1
    ADD = 2
    UPDATE = 3
    FORCEUPDATE = 4
    RENAME = 5
    SUBTITLE = 6
    REMOVE = 7

    names = {
        REFRESH: 'Refresh',
        ADD: 'Add',
        UPDATE: 'Update',
        FORCEUPDATE: 'Force Update',
        RENAME: 'Rename',
        SUBTITLE: 'Subtitle',
        REMOVE: 'Remove Show'
    }


class ShowQueueItem(srQueueItem):
    """
    Represents an item in the queue waiting to be executed

    Can be either:
    - show being added (may or may not be associated with a show object)
    - show being refreshed
    - show being updated
    - show being force updated
    - show being subtitled
    """

    def __init__(self, show, action_id):
        super(ShowQueueItem, self).__init__(ShowQueueActions.names[action_id], action_id)
        self.show = show
        self.startTime = time.time()

    def finish(self):
        self.show.flushEpisodes()

    def isInQueue(self):
        return self in sickrage.srCore.SHOWQUEUE.queue + [sickrage.srCore.SHOWQUEUE.currentItem]

    @property
    def show_name(self):
        return str(self.show.indexerid)

    @property
    def isLoading(self):
        return False


class QueueItemAdd(ShowQueueItem):
    def __init__(self, indexer, indexer_id, showDir, default_status, quality, flatten_folders, lang, subtitles,
                 subtitles_sr_metadata, anime, scene, paused, blacklist, whitelist, default_status_after, archive):
        super(QueueItemAdd, self).__init__(None, ShowQueueActions.ADD)

        self.indexer = indexer
        self.indexer_id = indexer_id
        self.showDir = showDir
        self.default_status = default_status
        self.quality = quality
        self.flatten_folders = flatten_folders
        self.lang = lang
        self.subtitles = subtitles
        self.subtitles_sr_metadata = subtitles_sr_metadata
        self.anime = anime
        self.scene = scene
        self.paused = paused
        self.blacklist = blacklist
        self.whitelist = whitelist
        self.default_status_after = default_status_after
        self.archive = archive
        self.priority = srQueuePriorities.HIGH

    @property
    def show_name(self):
        """
        Returns the show name if there is a show object created, if not returns
        the dir that the show is being added to.
        """
        if self.show is None:
            return os.path.basename(self.showDir)
        return self.show.name

    @property
    def isLoading(self):
        """
        Returns True if we've gotten far enough to have a show object, or False
        if we still only know the folder name.
        """
        if self.show is None:
            return True

    def run(self):
        sickrage.srCore.srLogger.info("Started adding show {} from show dir: {}".format(self.show_name, self.showDir))

        index_name = srIndexerApi(self.indexer).name

        # make sure the Indexer IDs are valid
        try:

            lINDEXER_API_PARMS = srIndexerApi(self.indexer).api_params.copy()
            lINDEXER_API_PARMS['cache'] = False
            lINDEXER_API_PARMS['language'] = self.lang or sickrage.srCore.srConfig.INDEXER_DEFAULT_LANGUAGE

            sickrage.srCore.srLogger.info("{}: {}".format(index_name, repr(lINDEXER_API_PARMS)))

            t = srIndexerApi(self.indexer).indexer(**lINDEXER_API_PARMS)
            s = t[self.indexer_id]
            if not s: return self._finishEarly()

            # this usually only happens if they have an NFO in their show dir which gave us a Indexer ID that has no proper english version of the show
            if not getattr(s, 'seriesname'):
                sickrage.srCore.srLogger.error(
                    "Show in {} has no name on {}, probably the wrong language used to search with".format(self.showDir,
                                                                                                           index_name))
                sickrage.srCore.srNotifications.error("Unable to add show",
                                                      "Show in {} has no name on {}, probably the wrong language. Delete .nfo and add manually in the correct language".format(
                                                          self.showDir, index_name))
                return self._finishEarly()

            # if the show has no episodes/seasons
            if not len(s):
                sickrage.srCore.srLogger.error("Show " + str(s['seriesname']) + " is on " + str(
                    srIndexerApi(self.indexer).name) + " but contains no season/episode data.")
                sickrage.srCore.srNotifications.error("Unable to add show",
                                                      "Show " + str(s['seriesname']) + " is on " + str(srIndexerApi(
                                                          self.indexer).name) + " but contains no season/episode data.")
                return self._finishEarly()
        except Exception as e:
            sickrage.srCore.srLogger.error(
                "{}: Error while loading information from indexer {}. Error: {}".format(self.indexer_id, index_name,
                                                                                        e.message))

            sickrage.srCore.srNotifications.error(
                "Unable to add show",
                "Unable to look up the show in {} on {} using ID {}, not using the NFO. Delete .nfo and try adding manually again.".format(
                    self.showDir, index_name, self.indexer_id)
            )

            if sickrage.srCore.srConfig.USE_TRAKT:
                title = self.showDir.split("/")[-1]

                data = {
                    'shows': [
                        {
                            'title': title,
                            'ids': {srIndexerApi(self.indexer).trakt_id: self.indexer_id}
                        }
                    ]
                }

                srTraktAPI()["sync/watchlist"].remove(data)

            return self._finishEarly()

        try:
            self.show = TVShow(self.indexer, self.indexer_id, self.lang)

            self.show.loadFromIndexer()

            # set up initial values
            self.show.location = self.showDir
            self.show.subtitles = self.subtitles or sickrage.srCore.srConfig.SUBTITLES_DEFAULT
            self.show.subtitles_sr_metadata = self.subtitles_sr_metadata
            self.show.quality = self.quality or sickrage.srCore.srConfig.QUALITY_DEFAULT
            self.show.flatten_folders = self.flatten_folders or sickrage.srCore.srConfig.FLATTEN_FOLDERS_DEFAULT
            self.show.anime = self.anime or sickrage.srCore.srConfig.ANIME_DEFAULT
            self.show.scene = self.scene or sickrage.srCore.srConfig.SCENE_DEFAULT
            self.show.archive_firstmatch = self.archive or sickrage.srCore.srConfig.ARCHIVE_DEFAULT
            self.show.paused = self.paused or False

            # set up default new/missing episode status
            sickrage.srCore.srLogger.info(
                "Setting all current episodes to the specified default status: " + str(self.default_status))

            self.show.default_ep_status = self.default_status

            if self.show.anime:
                self.show.release_groups = BlackAndWhiteList(self.show.indexerid)
                if self.blacklist:
                    self.show.release_groups.set_black_keywords(self.blacklist)
                if self.whitelist:
                    self.show.release_groups.set_white_keywords(self.whitelist)

                    # # be smartish about this
                    # if self.show.genre and "talk show" in self.show.genre.lower():
                    #     self.show.air_by_date = 1
                    # if self.show.genre and "documentary" in self.show.genre.lower():
                    #     self.show.air_by_date = 0
                    # if self.show.classification and "sports" in self.show.classification.lower():
                    #     self.show.sports = 1

        except indexer_exception as e:
            sickrage.srCore.srLogger.error(
                "Unable to add show due to an error with " + srIndexerApi(
                    self.indexer).name + ": {}".format(e.message))
            if self.show:
                sickrage.srCore.srNotifications.error(
                    "Unable to add " + str(self.show.name) + " due to an error with " + srIndexerApi(
                        self.indexer).name + "")
            else:
                sickrage.srCore.srNotifications.error(
                    "Unable to add show due to an error with " + srIndexerApi(self.indexer).name + "")
            return self._finishEarly()

        except MultipleShowObjectsException:
            sickrage.srCore.srLogger.warning("The show in " + self.showDir + " is already in your show list, skipping")
            sickrage.srCore.srNotifications.error('Show skipped',
                                                  "The show in " + self.showDir + " is already in your show list")
            return self._finishEarly()

        except Exception as e:
            sickrage.srCore.srLogger.error("Error trying to add show: {}".format(e.message))
            sickrage.srCore.srLogger.debug(traceback.format_exc())
            raise self._finishEarly()

        try:
            sickrage.srCore.srLogger.debug("Attempting to retrieve show info from IMDb")
            self.show.loadIMDbInfo()
        except Exception as e:
            sickrage.srCore.srLogger.error("Error loading IMDb info: {}".format(e.message))

        try:
            self.show.saveToDB()
        except Exception as e:
            sickrage.srCore.srLogger.error("Error saving the show to the database: {}".format(e.message))
            sickrage.srCore.srLogger.debug(traceback.format_exc())
            raise self._finishEarly()

        # add it to the show list
        if not findCertainShow(sickrage.srCore.SHOWLIST, self.indexer_id): sickrage.srCore.SHOWLIST.append(self.show)

        try:
            self.show.loadEpisodesFromIndexer()
        except Exception as e:
            sickrage.srCore.srLogger.error(
                "Error with " + srIndexerApi(
                    self.show.indexer).name + ", not creating episode list: {}".format(e.message))
            sickrage.srCore.srLogger.debug(traceback.format_exc())

        try:
            self.show.loadEpisodesFromDir()
        except Exception as e:
            sickrage.srCore.srLogger.debug("Error searching dir for episodes: {}".format(e.message))
            sickrage.srCore.srLogger.debug(traceback.format_exc())

        # if they set default ep status to WANTED then run the backlog to search for episodes
        if self.show.default_ep_status == WANTED:
            sickrage.srCore.srLogger.info("Launching backlog for this show since its episodes are WANTED")
            sickrage.srCore.BACKLOGSEARCHER.searchBacklog([self.show])

        self.show.writeMetadata()
        self.show.updateMetadata()
        self.show.populateCache()

        if sickrage.srCore.srConfig.USE_TRAKT:
            # if there are specific episodes that need to be added by trakt
            sickrage.srCore.TRAKTSEARCHER.manageNewShow(self.show)

            # add show to trakt.tv library
            if sickrage.srCore.srConfig.TRAKT_SYNC:
                sickrage.srCore.TRAKTSEARCHER.addShowToTraktLibrary(self.show)

            if sickrage.srCore.srConfig.TRAKT_SYNC_WATCHLIST:
                sickrage.srCore.srLogger.info("update watchlist")
                sickrage.srCore.notifiersDict['trakt'].update_watchlist(show_obj=self.show)

        # Load XEM data to DB for show
        xem_refresh(self.show.indexerid, self.show.indexer, force=True)

        # check if show has XEM mapping so we can determin if searches should go by scene numbering or indexer numbering.
        if not self.scene and get_xem_numbering_for_show(self.show.indexerid, self.show.indexer):
            self.show.scene = 1

        self.show.default_ep_status = self.default_status_after

        self.show.saveToDB()

        sickrage.srCore.NAMECACHE.build(self.show)

        self.finish()

        sickrage.srCore.srLogger.info(
            "Finished adding show {} in {}s from show dir: {}".format(self.show_name,
                                                                      round(time.time() - self.startTime, 2),
                                                                      self.showDir))

    def _finishEarly(self):
        if self.show: sickrage.srCore.SHOWQUEUE.removeShow(self.show)


class QueueItemRefresh(ShowQueueItem):
    def __init__(self, show=None, force=False):
        super(QueueItemRefresh, self).__init__(show, ShowQueueActions.REFRESH)

        # force refresh certain items
        self.force = force

    def run(self):
        sickrage.srCore.srLogger.info("Performing refresh for show: {}".format(self.show.name))

        self.show.refreshDir()

        self.show.writeMetadata()

        if self.force:
            self.show.updateMetadata()

        self.show.populateCache(self.force)

        # Load XEM data to DB for show
        xem_refresh(self.show.indexerid, self.show.indexer)

        self.show.last_refresh = datetime.date.today().toordinal()

        self.finish()

        sickrage.srCore.srLogger.info(
            "Finished refresh in {}s for show: {}".format(round(time.time() - self.startTime, 2), self.show.name))


class QueueItemRename(ShowQueueItem):
    def __init__(self, show=None):
        super(QueueItemRename, self).__init__(show, ShowQueueActions.RENAME)

    def run(self):
        sickrage.srCore.srLogger.info("Performing renames for show: {}".format(self.show.name))

        if not os.path.isdir(self.show.location):
            sickrage.srCore.srLogger.warning(
                "Can't perform rename on " + self.show.name + " when the show dir is missing.")
            return

        ep_obj_rename_list = []

        ep_obj_list = self.show.getAllEpisodes(has_location=True)
        for cur_ep_obj in ep_obj_list:
            # Only want to rename if we have a location
            if cur_ep_obj.location:
                if cur_ep_obj.relatedEps:
                    # do we have one of multi-episodes in the rename list already
                    have_already = False
                    for cur_related_ep in cur_ep_obj.relatedEps + [cur_ep_obj]:
                        if cur_related_ep in ep_obj_rename_list:
                            have_already = True
                            break
                    if not have_already:
                        ep_obj_rename_list.append(cur_ep_obj)

                else:
                    ep_obj_rename_list.append(cur_ep_obj)

        for cur_ep_obj in ep_obj_rename_list:
            cur_ep_obj.rename()

        self.finish()

        sickrage.srCore.srLogger.info("Finished renames for show: {}".format(self.show.name))


class QueueItemSubtitle(ShowQueueItem):
    def __init__(self, show=None):
        super(QueueItemSubtitle, self).__init__(show, ShowQueueActions.SUBTITLE)

    def run(self):
        sickrage.srCore.srLogger.info("Started downloading subtitles for show: {}".format(self.show.name))

        self.show.downloadSubtitles()

        self.finish()

        sickrage.srCore.srLogger.info("Finished downloading subtitles for show: {}".format(self.show.name))


class QueueItemUpdate(ShowQueueItem):
    def __init__(self, show=None, action_id=ShowQueueActions.UPDATE):
        super(QueueItemUpdate, self).__init__(show, action_id)
        self.force = False

    def run(self):
        sickrage.srCore.srLogger.info("Performing updates for show: {}".format(self.show.name))

        try:
            sickrage.srCore.srLogger.debug("Retrieving show info from " + srIndexerApi(self.show.indexer).name + "")
            self.show.loadFromIndexer(cache=False)
        except indexer_error as e:
            sickrage.srCore.srLogger.warning(
                "Unable to contact " + srIndexerApi(self.show.indexer).name + ", aborting: {}".format(e.message))
            return
        except indexer_attributenotfound as e:
            sickrage.srCore.srLogger.error(
                "Data retrieved from " + srIndexerApi(self.show.indexer).name + " was incomplete, aborting: {}".format(
                    e.message))
            return

        try:
            sickrage.srCore.srLogger.debug("Attempting to retrieve show info from IMDb")
            self.show.loadIMDbInfo()
        except Exception as e:
            sickrage.srCore.srLogger.error("Error loading IMDb info: {}".format(e.message))
            sickrage.srCore.srLogger.debug(traceback.format_exc())

        # get episode list from DB
        DBEpList = self.show.loadEpisodesFromDB()
        IndexerEpList = None

        # get episode list from TVDB
        try:
            IndexerEpList = self.show.loadEpisodesFromIndexer()
        except indexer_exception as e:
            sickrage.srCore.srLogger.error("Unable to get info from " + srIndexerApi(
                self.show.indexer).name + ", the show info will not be refreshed: {}".format(e.message))

        if IndexerEpList is None:
            sickrage.srCore.srLogger.error("No data returned from " + srIndexerApi(
                self.show.indexer).name + ", unable to update this show")
        else:
            # for each ep we found on indexer delete it from the DB list
            for curSeason in IndexerEpList:
                for curEpisode in IndexerEpList[curSeason]:
                    if curSeason in DBEpList and curEpisode in DBEpList[curSeason]:
                        del DBEpList[curSeason][curEpisode]

            # remaining episodes in the DB list are not on the indexer, just delete them from the DB
            for curSeason in DBEpList:
                for curEpisode in DBEpList[curSeason]:
                    sickrage.srCore.srLogger.info(
                        "Permanently deleting episode " + str(curSeason) + "x" + str(curEpisode) + " from the database")

                    curEp = self.show.getEpisode(curSeason, curEpisode)
                    try:
                        curEp.deleteEpisode()
                    except EpisodeDeletedException:
                        pass

        # cleanup
        scrub(DBEpList)
        scrub(IndexerEpList)

        sickrage.srCore.srLogger.info(
            "Finished updates in {}s for show: {}".format(round(time.time() - self.startTime, 2), self.show.name))

        # refresh show
        sickrage.srCore.SHOWQUEUE.refreshShow(self.show, self.force)


class QueueItemForceUpdate(QueueItemUpdate):
    def __init__(self, show=None):
        super(QueueItemForceUpdate, self).__init__(show, ShowQueueActions.FORCEUPDATE)
        self.force = True


class QueueItemRemove(ShowQueueItem):
    def __init__(self, show=None, full=False):
        super(QueueItemRemove, self).__init__(show, ShowQueueActions.REMOVE)

        # lets make sure this happens before any other high priority actions
        self.priority = srQueuePriorities.HIGH + srQueuePriorities.HIGH
        self.full = full

    def run(self):
        sickrage.srCore.srLogger.info("Removing show: {}".format(self.show.name))

        self.show.deleteShow(full=self.full)

        if sickrage.srCore.srConfig.USE_TRAKT:
            try:
                sickrage.srCore.TRAKTSEARCHER.removeShowFromTraktLibrary(self.show)
            except Exception as e:
                sickrage.srCore.srLogger.warning(
                    "Unable to delete show from Trakt: %s. Error: %s" % (self.show.name, e))

        self.finish()

        sickrage.srCore.srLogger.info("Finished removing show: {}".format(self.show.name))
