"""Shared set-up for the verify_*.py scripts' --offline mode.

Two things the in-memory stand-in (mongomock) needs before a suite runs:

1. It has to be the *only* database the app can reach. The suites point the
   collections they test at mongomock, but the app's startup (index creation,
   the file-hash backfill) and any collection a suite does not override went
   through `app.mongodb.get_client()` -- which is the real server named in
   .env. Installing the mongomock client as that shared client means every
   getter in app.mongodb resolves inside memory, so an offline run cannot
   read or write a real database at all.

2. It has to understand `array_filters`. Deleting a document clears the
   pointers nested records hold to it with an update like

       {"$set": {"cvs.$[item].document_id": None, ...}},
       array_filters=[{"item.document_id": "<id>"}]

   which real MongoDB supports and mongomock does not ("Array filters are not
   implemented in mongomock yet"). The emulation below covers exactly that
   shape -- `$set` through `$[name]` with equality filters -- and refuses
   anything else loudly, so it can never quietly pretend to support an update
   it does not understand. Only mongomock is patched; the app is unchanged.

3. It has to understand the positional projection `<array>.$`. Before a CV
   or certification is changed, the employees router reads which document it
   points at now with

       find_one({"_id": ..., "cvs.id": "<item id>"}, {"cvs.$": 1})

   which returns the employee with only the one matching element in `cvs`.
   mongomock refuses it ("Positional projection is not implemented in
   mongomock"). The emulation covers that shape -- a find_one whose filter
   picks the element by equality on `<array>.<subfield>` -- and refuses any
   other positional projection the same loud way.
"""

import copy

_PATCHED = False


def install(client):
    """Make `client` (a mongomock.MongoClient) the app's only database and
    teach mongomock the array_filters updates the app uses. Returns it."""
    from app import mongodb

    _patch_array_filters()
    _patch_positional_projection()
    mongodb._client = client
    return client


def _conditions(array_filters):
    """{identifier: {subfield: value}} from filters like {"item.document_id": x}."""
    conditions = {}
    for entry in array_filters:
        for key, value in entry.items():
            identifier, dot, sub = key.partition(".")
            if not dot or not sub or isinstance(value, dict) or sub.startswith("$"):
                raise NotImplementedError(
                    f"offline array_filters: only equality filters on a subfield are emulated, got {entry!r}"
                )
            conditions.setdefault(identifier, {})[sub] = value
    return conditions


def _get(element, path):
    for part in path.split("."):
        if not isinstance(element, dict) or part not in element:
            return None
        element = element[part]
    return element


def _set(element, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        element = element.setdefault(part, {})
    element[parts[-1]] = value


def _emulate(collection, filter, update, array_filters, many):
    from pymongo.results import UpdateResult

    unsupported = [op for op in update if op != "$set"]
    if unsupported:
        raise NotImplementedError(
            f"offline array_filters: only $set is emulated, got {unsupported}"
        )
    conditions = _conditions(array_filters)
    plain, targeted = {}, []
    for path, value in update["$set"].items():
        if ".$[" not in path:
            plain[path] = value
            continue
        array_field, _, rest = path.partition(".$[")
        identifier, _, sub = rest.partition("].")
        if identifier not in conditions or not sub or "$" in array_field or "$" in sub:
            raise NotImplementedError(f"offline array_filters: cannot emulate path {path!r}")
        targeted.append((array_field, identifier, sub, value))

    documents = list(collection.find(filter))
    if not many:
        documents = documents[:1]
    modified = 0
    for document in documents:
        arrays = {}
        # Which elements match is decided against the document as stored,
        # before any path is written -- as MongoDB does. Deciding as the
        # writes happen would let clearing `document_id` stop the same
        # update from also clearing `file_name`.
        matches = {}
        for array_field, identifier, _, _ in targeted:
            original = document.get(array_field) or []
            matches[(array_field, identifier)] = [
                isinstance(element, dict)
                and all(_get(element, key) == wanted for key, wanted in conditions[identifier].items())
                for element in original
            ]
        for array_field, identifier, sub, value in targeted:
            items = arrays.setdefault(array_field, copy.deepcopy(document.get(array_field) or []))
            for element, matched in zip(items, matches[(array_field, identifier)]):
                if matched:
                    _set(element, sub, value)
        changes = {**plain, **arrays}
        result = collection.update_one({"_id": document["_id"]}, {"$set": changes})
        modified += result.modified_count
    return UpdateResult({"n": len(documents), "nModified": modified, "ok": 1.0}, acknowledged=True)


def _patch_array_filters():
    global _PATCHED
    if _PATCHED:
        return
    from mongomock.collection import Collection

    original_update_one = Collection.update_one
    original_update_many = Collection.update_many

    def update_one(self, filter, update, *args, array_filters=None, **kwargs):
        if array_filters:
            return _emulate(self, filter, update, array_filters, many=False)
        return original_update_one(self, filter, update, *args, **kwargs)

    def update_many(self, filter, update, *args, array_filters=None, **kwargs):
        if array_filters:
            return _emulate(self, filter, update, array_filters, many=True)
        return original_update_many(self, filter, update, *args, **kwargs)

    Collection.update_one = update_one
    Collection.update_many = update_many
    _PATCHED = True


# --- positional projection ------------------------------------------------------

_PROJECTION_PATCHED = False


def _positional_find_one(collection, original_find_one, filter, projection, args, kwargs):
    """find_one with a projection holding `<array>.$`, as MongoDB answers it:
    the document with that array cut down to the first element the filter's
    condition on the array matched, plus whatever else the projection asks
    for."""
    positional = [key for key in projection if key.endswith(".$")]
    if len(positional) != 1 or projection[positional[0]] not in (1, True):
        raise NotImplementedError(
            f"offline projection: only one inclusive `<array>.$` is emulated, got {projection!r}"
        )
    array_field = positional[0][: -len(".$")]
    if "." in array_field or "$" in array_field:
        raise NotImplementedError(f"offline projection: cannot emulate {positional[0]!r}")

    # The element the query matched is the one its conditions on this array
    # describe. Only plain equality on a subfield is emulated.
    conditions = {}
    for key, value in (filter or {}).items():
        if key.startswith(array_field + "."):
            if isinstance(value, dict):
                raise NotImplementedError(
                    f"offline projection: only equality conditions are emulated, got {key!r}: {value!r}"
                )
            conditions[key[len(array_field) + 1:]] = value
    if not conditions:
        raise NotImplementedError(
            f"offline projection: {positional[0]!r} needs a condition on {array_field!r} in the filter"
        )
    if any(key.startswith("$") for key in filter):
        raise NotImplementedError("offline projection: top-level operators in the filter are not emulated")

    plain = {key: value for key, value in projection.items() if key != positional[0]}
    if any(value in (0, False) for value in plain.values()):
        raise NotImplementedError("offline projection: exclusions beside `.$` are not emulated")
    plain[array_field] = 1

    document = original_find_one(collection, filter, plain, *args, **kwargs)
    if document is None:
        return None
    items = document.get(array_field) or []
    matched = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and all(_get(item, key) == wanted for key, wanted in conditions.items())
        ),
        None,
    )
    document[array_field] = [matched] if matched is not None else []
    return document


def _patch_positional_projection():
    global _PROJECTION_PATCHED
    if _PROJECTION_PATCHED:
        return
    from mongomock.collection import Collection

    original_find_one = Collection.find_one

    def find_one(self, filter=None, *args, **kwargs):
        projection = kwargs.pop("projection", None)
        if projection is None and args:
            projection, args = args[0], args[1:]
        if isinstance(projection, dict) and any(key.endswith(".$") for key in projection):
            return _positional_find_one(self, original_find_one, filter, projection, args, kwargs)
        if projection is not None:
            kwargs["projection"] = projection
        return original_find_one(self, filter, *args, **kwargs)

    Collection.find_one = find_one
    _PROJECTION_PATCHED = True

