# vim: set et sw=4 sts=4:

# Copyright 2012 Dave Hughes.
#
# This file is part of tvrip.
#
# tvrip is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# tvrip is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# tvrip.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import (
    unicode_literals, print_function, absolute_import, division)

import sys
import os
import re
import shutil
import logging
import tempfile
import shutil
import hashlib
from datetime import datetime, date, time, timedelta, MINYEAR
from operator import attrgetter
from itertools import groupby
from subprocess import Popen, PIPE, STDOUT
from tvrip.database import Configuration
from tvrip.subtitles import Subtitle, Subtitles, SubtitleCorrections

AUDIO_MIX_ORDER = [u'5.1 ch', u'5.0 ch', u'Dolby Surround', u'2.0 ch', u'1.0 ch']
AUDIO_ENCODING_ORDER = [u'DTS', u'AC3']


class Error(Exception):
    u"""Base class for ripper errors"""

class ProcessError(Error):
    u"""Class for errors returned by external processes"""


class Disc(object):
    u"""Represents a DVD disc"""

    def __init__(self):
        super(Disc, self).__init__()
        self.titles = []
        self.serial = None
        self.ident = None
        self.match = None

    def test(self, pattern, line):
        self.match = pattern.match(line)
        return self.match

    def __repr__(self):
        return u"<Disc()>"

    error1_re = re.compile(ur"libdvdread: Can't open .* for reading")
    error2_re = re.compile(ur'libdvdnav: vm: failed to open/read the DVD')
    disc_serial_re = re.compile(ur'^libdvdnav: DVD Serial Number: (?P<serial>.*)$')
    title_re = re.compile(ur'^\+ title (?P<number>\d+):$')
    duration_re = re.compile(ur'^  \+ duration: (?P<duration>.*)$')
    stats_re = re.compile(ur'^  \+ size: (?P<size>.*), aspect: (?P<aspect_ratio>.*), (?P<frame_rate>.*) fps$')
    crop_re = re.compile(ur'^  \+ autocrop: (?P<crop>.*)$')
    comb_re = re.compile(ur'^  \+ combing detected,.*$')
    chapters_re = re.compile(ur'^  \+ chapters:$')
    chapter_re = re.compile(ur'^    \+ (?P<number>\d+): cells \d+->\d+, \d+ blocks, duration (?P<duration>.*)$')
    audio_tracks_re = re.compile(ur'^  \+ audio tracks:$')
    audio_track_re = re.compile(ur'^    \+ (?P<number>\d+), (?P<name>[^(]*) \((?P<encoding>[^)]*)\)( \((?P<label>[^)]*)\))? \((?P<channel_mix>[^)]*)\) \(iso639-2: (?P<language>[a-z]{2,3})\), (?P<sample_rate>\d+)Hz, (?P<bit_rate>\d+)bps$')
    subtitle_tracks_re = re.compile(ur'^  \+ subtitle tracks:$')
    subtitle_track_re = re.compile(ur'^    \+ (?P<number>\d+), (?P<name>.*) \(iso639-2: (?P<language>[a-z]{2,3})\)( \((?P<type>.*)\))?$')
    def scan(self, config):
        self.titles = []
        cmdline = [
            config.get_path(u'handbrake'),
            u'-i', config.source, # specify the input device
            u'-t', u'0'    # ask for a scan of the entire disc
        ]
        process = Popen(cmdline, stdout=PIPE, stderr=STDOUT)
        output = process.communicate()[0]
        state = set([u'disc'])
        title = None
        # Parse the output into child objects
        for line in output.splitlines():
            if u'disc' in state and (
                    self.test(self.error1_re, line) or
                    self.test(self.error2_re, line)
                ):
                raise IOError('Unable to read disc in %s' % config.source)
            if u'disc' in state and self.test(self.disc_serial_re, line):
                self.serial = self.match.group(u'serial')
            elif u'disc' in state and self.test(self.title_re, line):
                if title:
                    title.chapters = sorted(title.chapters, key=attrgetter(u'number'))
                    title.audio_tracks = sorted(title.audio_tracks, key=attrgetter(u'number'))
                    title.subtitle_tracks = sorted(title.subtitle_tracks, key=attrgetter(u'number'))
                state = set([u'disc', u'title'])
                title = Title(self)
                title.number = int(self.match.group(u'number'))
            elif u'title' in state and self.test(self.duration_re, line):
                state = set([u'disc', u'title'])
                hours, minutes, seconds = (int(i) for i in self.match.group(u'duration').split(u':'))
                title.duration = timedelta(seconds=seconds, minutes=minutes, hours=hours)
            elif u'title' in state and self.test(self.stats_re, line):
                state = set([u'disc', u'title'])
                title.size = (int(i) for i in self.match.group(u'size').split(u'x'))
                title.aspect_ratio = float(self.match.group(u'aspect_ratio'))
                title.frame_rate = float(self.match.group(u'frame_rate'))
            elif u'title' in state and self.test(self.crop_re, line):
                state = set([u'disc', u'title'])
                title.crop = (int(i) for i in self.match.group(u'crop').split(u'/'))
            elif u'title' in state and self.test(self.comb_re, line):
                title.interlaced = True
            elif u'title' in state and self.test(self.chapters_re, line):
                state = set([u'disc', u'title', u'chapter'])
            elif u'chapter' in state and self.test(self.chapter_re, line):
                chapter = Chapter(title)
                chapter.number = int(self.match.group(u'number'))
                hours, minutes, seconds = (int(i) for i in self.match.group(u'duration').split(u':'))
                chapter.duration = timedelta(seconds=seconds, minutes=minutes, hours=hours)
            elif u'title' in state and self.test(self.audio_tracks_re, line):
                state = set([u'disc', u'title', u'audio'])
            elif u'audio' in state and self.test(self.audio_track_re, line):
                track = AudioTrack(title)
                track.number = int(self.match.group(u'number'))
                if self.match.group(u'label'):
                    track.name = '%s (%s)' % (
                        self.match.group(u'name'),
                        self.match.group(u'label'),
                    )
                else:
                    track.name = self.match.group(u'name')
                track.language = self.match.group(u'language')
                track.encoding = self.match.group(u'encoding')
                track.channel_mix = self.match.group(u'channel_mix')
                track.sample_rate = int(self.match.group(u'sample_rate'))
                track.bit_rate = int(self.match.group(u'bit_rate'))
            elif u'title' in state and self.test(self.subtitle_tracks_re, line):
                state = set([u'disc', u'title', u'subtitle'])
            elif u'subtitle' in state and self.test(self.subtitle_track_re, line):
                track = SubtitleTrack(title)
                track.number = int(self.match.group(u'number'))
                track.name = self.match.group(u'name')
                track.language = self.match.group(u'language')
                track.type = self.match.group(u'type')
        self.titles = sorted(self.titles, key=attrgetter(u'number'))
        # Determine the best audio and subtitle tracks
        for title in self.titles:
            for key, group in groupby(sorted(title.audio_tracks, key=attrgetter('name')), key=attrgetter('name')):
                group = sorted(group, key=lambda track: (
                    AUDIO_MIX_ORDER.index(track.channel_mix),
                    AUDIO_ENCODING_ORDER.index(track.encoding)
                ))
                if group:
                    group[0].best = True
            for key, group in groupby(sorted(title.subtitle_tracks, key=attrgetter('name')), key=attrgetter('name')):
                group = list(group)
                if group:
                    group[0].best = True
        # Calculate a hash of disc serial, and track properties to form a
        # unique disc identifier, then replace disc-serial with this (#1)
        h = hashlib.sha1()
        h.update(self.serial)
        h.update(str(len(self.titles)))
        for title in self.titles:
            h.update(str(title.duration))
            h.update(str(len(title.chapters)))
            for chapter in title.chapters:
                h.update(str(chapter.start))
                h.update(str(chapter.duration))
        self.ident = '$H1$' + h.hexdigest()

    def rip(self, config, episode, title, audio_tracks, subtitle_tracks, start_chapter=None, end_chapter=None):
        if not isinstance(config, Configuration):
            raise ValueError(u'config must a Configuration instance')
        filename = config.template.format(
            program=config.program.name,
            season=config.season.number,
            episode=episode.number,
            name=episode.name,
            now=datetime.now(),
        )
        # Convert the subtitle track(s) if required
        if config.subtitle_format == u'subrip':
            for track in subtitle_tracks:
                assert track.title is title
                track.convert(config, filename)
        # Convert the video track
        audio_defs = [
            (track.number, config.audio_mix, track.name)
            for track in audio_tracks
        ]
        subtitle_defs = [
            (track.number, track.name)
            for track in subtitle_tracks
        ]
        cmdline = [
            config.get_path(u'handbrake'),
            u'-i', config.source,
            u'-t', unicode(title.number),
            u'-o', os.path.join(config.target, filename),
            u'-f', u'mp4',          # output an MP4 container
            u'-O',                  # optimize for streaming
            u'-m',                  # include chapter markers
            u'--strict-anamorphic', # store pixel aspect ratio
            u'-e', u'x264',         # use x264 for encoding
            u'-q', u'23',           # quality 23
            u'-x', u'b-adapt=2:rc-lookahead=50', # advanced encoding options (mostly defaults from High Profile)
            u'-a', u','.join(unicode(num) for (num, _, _)  in audio_defs),
            u'-6', u','.join(mix          for (_, mix, _)  in audio_defs),
            u'-A', u','.join(name         for (_, _, name) in audio_defs),
        ]
        if start_chapter:
            cmdline.append(u'-c')
            if end_chapter:
                cmdline.append(u'%d-%d' % (start_chapter.number, end_chapter.number))
            else:
                cmdline.append(unicode(start_chapter.number))
        if config.subtitle_format == u'vobsub':
            cmdline.append(u'-s')
            cmdline.append(u','.join(unicode(num) for (num, _) in subtitle_defs))
        if config.decomb == u'on':
            cmdline.append(u'-d')
            cmdline.append(u'slow')
        elif config.decomb == u'auto':
            cmdline.append(u'-5')
        p = Popen(cmdline, stdout=sys.stdout, stderr=sys.stderr)
        p.communicate()
        if p.returncode != 0:
            raise ValueError(u'Handbrake exited with non-zero return code %d' % p.returncode)
        # Tag the resulting file
        tmphandle, tmpfile = tempfile.mkstemp(dir=config.temp)
        try:
            cmdline = [
                config.get_path(u'atomicparsley'),
                os.path.join(config.target, filename),
                u'-o', tmpfile,
                u'--stik', u'TV Show',
                # set tags for TV shows
                u'--TVShowName',   episode.season.program.name,
                u'--TVSeasonNum',  unicode(episode.season.number),
                u'--TVEpisodeNum', unicode(episode.number),
                u'--TVEpisode',    episode.name,
                # also set tags for music files as these have wider support
                u'--artist',       episode.season.program.name,
                u'--album',        u'Season %d' % episode.season.number,
                u'--tracknum',     unicode(episode.number),
                u'--title',        episode.name
            ]
            p = Popen(cmdline, stdout=sys.stdout, stderr=sys.stderr)
            p.communicate()
            if p.returncode != 0:
                raise ValueError('AtomicParsley exited with non-zero return code %d' % p.returncode)
            os.chmod(tmpfile, os.stat(os.path.join(config.target, filename)).st_mode)
            shutil.move(tmpfile, os.path.join(config.target, filename))
        finally:
            os.close(tmphandle)

class Title(object):
    u"""Represents a title on a DVD"""

    def __init__(self, disc):
        super(Title, self).__init__()
        disc.titles.append(self)
        self.disc = disc
        self.number = 0
        self.duration = timedelta()
        self.size = (0, 0)
        self.aspect_ratio = 0
        self.frame_rate = 0
        self.crop = (0, 0, 0, 0)
        self.chapters = []
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.interlaced = False

    def __repr__(self):
        return u"<Title(%d)>" % self.number


class Chapter(object):
    u"""Represents a chapter marker within a Title object"""

    def __init__(self, title):
        super(Chapter, self).__init__()
        title.chapters.append(self)
        self.title = title
        self.number = 0
        self.duration = timedelta(0)

    @property
    def start(self):
        result = datetime(MINYEAR, 1, 1)
        for c in self.title.chapters:
            if c.number >= self.number:
                break
            result += c.duration
        return result.time()

    @property
    def finish(self):
        result = datetime.combine(date(MINYEAR, 1, 1), self.start)
        return (result + self.duration).time()

    def __repr__(self):
        return u"<Chapter(%d, %s)>" % (self.number, self.duration)


class AudioTrack(object):
    u"""Represents an audio track within a Title object"""

    def __init__(self, title):
        super(AudioTrack, self).__init__()
        title.audio_tracks.append(self)
        self.title = title
        self.name = u''
        self.number = 0
        self.language = u''
        self.encoding = u''
        self.channel_mix = u''
        self.sample_rate = 0
        self.bit_rate = 0
        self.best = False

    def __repr__(self):
        return u"<AudioTrack(%d, '%s')>" % (self.number, self.name)


class SubtitleTrack(object):
    u"""Represents a subtitle track within a Title object"""

    def __init__(self, title):
        super(SubtitleTrack, self).__init__()
        title.subtitle_tracks.append(self)
        self.title = title
        self.number = 0
        self.name = u''
        self.language = u''
        self.type = u''
        self.best = False
        self.log = u''
        self.corrections = SubtitleCorrections()

    def __repr__(self):
        return u"<SubtitleTrack(%d, '%s')>" % (self.number, self.name)


