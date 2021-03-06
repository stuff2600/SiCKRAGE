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

import os

import sickrage
from sickrage.core.caches.image_cache import ImageCache
from sickrage.core.media.banner import Banner
from sickrage.core.media.fanart import FanArt
from sickrage.core.media.network import Network
from sickrage.core.media.poster import Poster
from sickrage.indexers import srIndexerApi
from sickrage.indexers.config import INDEXER_TVDB
from sickrage.indexers.exceptions import indexer_error


def showImage(show=None, which=None):
    media_format = ('normal', 'thumb')[which in ('banner_thumb', 'poster_thumb', 'small')]

    if which[0:6] == 'banner':
        return Banner(show, media_format)
    elif which[0:6] == 'fanart':
        return FanArt(show, media_format)
    elif which[0:6] == 'poster':
        return Poster(show, media_format)
    elif which[0:7] == 'network':
        return Network(show, media_format)


def indexerImage(id=None, which=None):
    media_format = ('normal', 'thumb')[which in ('banner_thumb', 'poster_thumb', 'small')]
    image_type = which[0:6]

    if image_type not in ('fanart', 'poster', 'banner'):
        sickrage.srCore.srLogger.error(
            "Invalid image type " + str(image_type) + ", couldn't find it in the " + srIndexerApi(
                INDEXER_TVDB).name + " object")
        return

    try:
        lINDEXER_API_PARMS = srIndexerApi(INDEXER_TVDB).api_params.copy()
        t = srIndexerApi(INDEXER_TVDB).indexer(**lINDEXER_API_PARMS)

        image_name = str(id) + '.' + image_type + '.jpg'

        try:
            if media_format == "thumb":
                image_path = os.path.join(ImageCache()._thumbnails_dir(), image_name)
                if not os.path.exists(image_path):
                    image_url = t[int(id)]['_images'][image_type][0]['thumbnail']
                    sickrage.srCore.srWebSession.download(image_url, image_path)
            else:
                image_path = os.path.join(ImageCache()._cache_dir(), image_name)
                if not os.path.exists(image_path):
                    image_url = t[int(id)]['_images'][image_type][0]['filename']
                    sickrage.srCore.srWebSession.download(image_url, image_path)
        except KeyError:
            pass

        if image_type == 'banner':
            return Banner(int(id), media_format)
        elif image_type == 'fanart':
            return FanArt(int(id), media_format)
        elif image_type == 'poster':
            return Poster(int(id), media_format)
    except (indexer_error, IOError) as e:
        sickrage.srCore.srLogger.warning("{}: Unable to look up show on ".format(id) + srIndexerApi(
            INDEXER_TVDB).name + ", not downloading images: {}".format(e.message))
