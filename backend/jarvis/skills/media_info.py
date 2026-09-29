"""What's playing right now (Spotify, YouTube in a browser, VLC...), via Windows' media session API."""
import logging

log = logging.getLogger("media")


async def now_playing():
    try:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        mgr = await Manager.request_async()
        session = mgr.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        status = {4: "playing", 5: "paused"}.get(int(info.playback_status), "stopped")
        app = session.source_app_user_model_id.split("!")[0].split(".")[-1].replace(".exe", "")
        return {"title": props.title, "artist": props.artist, "album": props.album_title, "app": app, "status": status}
    except Exception as e:  # noqa: BLE001 - no session / API unavailable
        log.info("now playing unavailable: %s", e)
        return None
