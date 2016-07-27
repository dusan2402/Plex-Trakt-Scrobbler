from plugin.sync.core.enums import SyncData, SyncMedia
from plugin.sync.core.guid import GuidParser
from plugin.sync.modes.core.base import log_unsupported, mark_unsupported
from plugin.sync.modes.pull.base import Base

from plex_database.models import MetadataItem
import elapsed
import logging

log = logging.getLogger(__name__)


class Shows(Base):
    data = [
        SyncData.Collection,
        SyncData.Playback,
        SyncData.Ratings,
        SyncData.Watched
    ]

    @elapsed.clock
    def run(self):
        # Retrieve show sections
        p_sections, p_sections_map = self.sections('show')

        # Fetch episodes with account settings
        p_shows, p_seasons, p_episodes = self.plex.library.episodes.mapped(
            p_sections, ([
                MetadataItem.library_section
            ], [], []),
            account=self.current.account.plex.key,
            parse_guid=True
        )

        # TODO process seasons

        # Calculate total number of episodes
        pending = {}

        for data in self.get_data(SyncMedia.Episodes):
            t_episodes = [
                (key, se, ep)
                for key, t_show in self.trakt[(SyncMedia.Episodes, data)].items()
                for se, t_season in t_show.seasons.items()
                for ep in t_season.episodes.iterkeys()
            ]

            if data not in pending:
                pending[data] = {}

            for key in t_episodes:
                pending[data][key] = False

        # Task started
        unsupported_shows = {}

        # Process shows
        for sh_id, guid, p_show in p_shows:
            # Parse guid
            match = GuidParser.parse(guid)

            if not match.supported:
                mark_unsupported(unsupported_shows, sh_id, guid)
                continue

            if not match.found:
                log.info('Unable to find identifier for: %s/%s (rating_key: %r)', guid.service, guid.id, sh_id)
                continue

            key = (match.guid.service, match.guid.id)

            # Try retrieve `pk` for `key`
            pk = self.trakt.table('shows').get(key)

            # Store in item map
            self.current.map.add(p_show.get('library_section'), sh_id, [key, pk])

            if pk is None:
                # No `pk` found
                continue

            # Execute data handlers
            for data in self.get_data(SyncMedia.Shows):
                t_show = self.trakt[(SyncMedia.Shows, data)].get(pk)

                # Execute show handlers
                self.execute_handlers(
                    SyncMedia.Shows, data,
                    key=sh_id,

                    p_item=p_show,
                    t_item=t_show
                )

        # Process episodes
        for ids, guid, (season_num, episode_num), p_show, p_season, p_episode in p_episodes:
            # Process `p_guid` (map + validate)
            match = GuidParser.parse(guid, (season_num, episode_num))

            if not match.supported:
                mark_unsupported(unsupported_shows, ids['show'], guid)
                continue

            if not match.found:
                log.info('Unable to find identifier for: %s/%s (rating_key: %r)', guid.service, guid.id, ids['show'])
                continue

            if not match.episodes:
                log.warn('No episodes returned for: %s/%s', guid.service, guid.id)
                continue

            key = (match.guid.service, match.guid.id)
            season_num, episode_num = match.episodes[0]

            # Try retrieve `pk` for `key`
            pk = self.trakt.table('shows').get(key)

            if pk is None:
                # No `pk` found
                continue

            if not ids.get('episode'):
                # Missing `episode` rating key
                continue

            for data in self.get_data(SyncMedia.Episodes):
                t_show = self.trakt[(SyncMedia.Episodes, data)].get(pk)

                if t_show is None:
                    # Unable to find matching show in trakt data
                    continue

                t_season = t_show.seasons.get(season_num)

                if t_season is None:
                    # Unable to find matching season in `t_show`
                    continue

                t_episode = t_season.episodes.get(episode_num)

                if t_episode is None:
                    # Unable to find matching episode in `t_season`
                    continue

                self.execute_handlers(
                    SyncMedia.Episodes, data,
                    key=ids['episode'],

                    p_item=p_episode,
                    t_item=t_episode
                )

                # Increment one step
                self.step(pending, data, (pk, season_num, episode_num))

            # Task checkpoint
            self.checkpoint()

        # Log details
        log_unsupported(log, 'Found %d unsupported show(s)', unsupported_shows)
        log.debug('Pending: %r', pending)
