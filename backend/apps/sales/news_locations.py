"""Responsibility: Validate optional source-backed locations for mapped news.
Implementation: Merge partial updates and require complete coordinates, city, country and evidence together.
Relationships: WorldNewsSerializer uses this validation before persistence; geocoding is an explicit collector/curation operation.
Directory:
- validate_news_location: Reject incomplete or unsubstantiated map-location payloads.
Variable index:
- logger: Record rejected field names without source bodies or credentials.
"""

import logging
import math
from urllib.parse import urlsplit
from rest_framework.exceptions import ValidationError

logger = logging.getLogger("salesmate.news_locations")


# Function: Validate an optional news map location.
# Inputs: `serializer` supplies the existing instance; `attrs` contains validated create/update fields.
# Outputs: Original attrs; inconsistent geography raises ValidationError with field names.
# Logic: Empty locations retain null coordinates. Known locations require city/country, both coordinates, and a public HTTPS source with a quote naming the city.
# Constraints: Internal evidence consistency is not external verification; no network lookup, headquarters inference or country-centroid substitution.
def validate_news_location(serializer, attrs):
    names = ("city", "country", "latitude", "longitude", "location_evidence", "location_source_url")
    record = {name: attrs.get(name, getattr(serializer.instance, name, None if name in {"latitude", "longitude"} else "")) for name in names}
    located = any(record[name] is not None for name in ("latitude", "longitude"))
    errors = {}
    if located:
        for name in ("latitude", "longitude"):
            if record[name] is not None and not math.isfinite(record[name]):
                errors[name] = "坐标必须是有限数值。"
        for name in names:
            if record[name] is None or (isinstance(record[name], str) and not record[name].strip()):
                errors[name] = "地图地点须同时提供城市、国家、经纬度和地点来源证据。"
        if record["city"] and record["city"].casefold() not in record["location_evidence"].casefold():
            errors["location_evidence"] = "地点证据须包含所填城市名称。"
        url = urlsplit(record["location_source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            errors["location_source_url"] = "地点来源须为无凭证的 HTTPS 链接。"
    elif any(record[name] for name in ("city", "location_evidence", "location_source_url")):
        errors["latitude"] = "有地点信息时须同时提供经纬度；无可靠位置时整组留空。"
    if errors:
        logger.warning("news_location_rejected fields=%s", ",".join(sorted(errors)))
        raise ValidationError(errors)
    return attrs
