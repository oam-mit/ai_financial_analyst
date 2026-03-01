import pusher
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Cache the pusher client
_pusher_client = None

def get_pusher_client():
    global _pusher_client
    if _pusher_client is None:
        try:
            _pusher_client = pusher.Pusher(
                app_id=settings.PUSHER_APP_ID,
                key=settings.PUSHER_KEY,
                secret=settings.PUSHER_SECRET,
                cluster=settings.PUSHER_CLUSTER,
                ssl=settings.PUSHER_SSL
            )
        except Exception as e:
            print(f"FAILED TO INIT PUSHER: {e}")
    return _pusher_client

def trigger_pusher_event(channel, event_name, data):
    try:
        pusher_client = get_pusher_client()
        if pusher_client:
            pusher_client.trigger(channel, event_name, data)
    except Exception as e:
        print(f"PUSHER TRIGGER ERROR: {e}")
