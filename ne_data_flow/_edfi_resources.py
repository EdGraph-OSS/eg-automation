from ed_fi.accessible_resources import BlacklistEntry, get_accessible_resources

NDE_BLACKLIST: list[BlacklistEntry] = [
    BlacklistEntry(module="edfixcrdc"),
    BlacklistEntry(module="ed-fi-x-learning-modality"),
    BlacklistEntry(module="ed-fi-xlearning-modality"),
    BlacklistEntry(module="ed-fi-xassessment-roster"),
    BlacklistEntry(resource="Descriptors"),
    BlacklistEntry(resource="contacts"),
    BlacklistEntry(resource="calendarDates"),
]


async def get_entities_value(base_url: str, client_id: str, client_secret: str) -> str:
    return await get_accessible_resources(base_url, client_id, client_secret, blacklist=NDE_BLACKLIST)
