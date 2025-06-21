import datetime

from homeassistant.helpers.template import Template


def timedelta_to_dict(td: datetime.timedelta) -> dict:
    """Convert a timedelta into a dict compatible with DurationSelector."""
    total = int(td.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return {"hours": hours, "minutes": minutes, "seconds": seconds}


def convert_to_safejson(data: any) -> any:
    if data is None:
        return None
    if isinstance(data, list):
        return [convert_to_safejson(val) for val in data]
    elif isinstance(data, dict):
        return {key: convert_to_safejson(val) for key, val in data.items()}
    elif isinstance(data, datetime.timedelta):
        return timedelta_to_dict(data)
    elif isinstance(data, datetime.time):
        return data.strftime("%H:%M:%S")
    elif isinstance(data, Template):
        return data.template
    return data
