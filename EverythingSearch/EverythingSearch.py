import sublime
import sublime_plugin
import urllib.request
import urllib.parse
import json
import os


# --- Constants ---

SETTINGS_DEFAULTS = {
    "url": "http://localhost:18081",
    "results_per_page": 10,
    "max_results": 300,
    "scope": "",
    "blacklist": [".dat", ".dll", ".sys", ".lnk", ".tmp"],
}

SETTINGS_TYPES = {
    "url": str,
    "results_per_page": int,
    "max_results": int,
    "scope": str,
    "blacklist": list,
}

KNOWN_BINARY_EXTENSIONS = {
    # Executables
    ".exe", ".bin", ".com", ".msi",
    # Archives
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".img", ".cab",
    # Audio
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
    # Video
    ".mp4", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".webm", ".m4v",
    # Image
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".tiff", ".tif", ".psd", ".webp", ".raw",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Compiled/Runtime
    ".class", ".o", ".so", ".dylib", ".pyc", ".pyo", ".obj", ".lib", ".a",
    # Database
    ".db", ".sqlite", ".mdb", ".accdb",
    # Disk images
    ".vmdk", ".vdi", ".vhd",
}

_last_query = {}  # prefix -> last query text


# --- Settings Helpers ---

def get_setting(key):
    settings = sublime.load_settings("Preferences.sublime-settings")
    return settings.get("et_" + key, SETTINGS_DEFAULTS.get(key))


def set_setting(key, value):
    settings = sublime.load_settings("Preferences.sublime-settings")
    settings.set("et_" + key, value)
    sublime.save_settings("Preferences.sublime-settings")


def get_everything_url():
    return get_setting("url").rstrip("/")


# --- Config Command Parser ---

def parse_config_command(text):
    if not text.startswith("config:"):
        return None
    rest = text[len("config:"):]
    if "=" not in rest:
        return None
    key, _, raw_value = rest.partition("=")
    key = key.strip()
    raw_value = raw_value.strip()
    if key not in SETTINGS_TYPES:
        return None
    return (key, raw_value)


def coerce_config_value(key, raw_value):
    expected_type = SETTINGS_TYPES.get(key)
    if expected_type is str:
        return (raw_value, None)
    elif expected_type is int:
        try:
            return (int(raw_value), None)
        except ValueError:
            return (None, "invalid value for {}".format(key))
    elif expected_type is list:
        items = [item.strip() for item in raw_value.split(",") if item.strip()]
        return (items, None)
    return (None, "unknown setting: {}".format(key))


def apply_config_command(text):
    parsed = parse_config_command(text)
    if parsed is None:
        return "EverythingSearch: invalid config command"
    key, raw_value = parsed
    value, error = coerce_config_value(key, raw_value)
    if error:
        return "EverythingSearch: " + error
    set_setting(key, value)
    return "EverythingSearch: {} set to {}".format(key, value)


# --- Query Builder ---

def build_query(user_input, prefix=""):
    query = user_input.strip()

    if query.startswith('"') and query.endswith('"') and query.count('"') == 2:
        query = query[1:-1]

    if prefix:
        query = prefix + query if prefix.endswith(":") else prefix + " " + query

    scope = get_setting("scope")
    if scope and "path:" not in query:
        query = "path:{} {}".format(scope, query)

    query = "file: " + query
    return query


# --- HTTP Client ---

def search_everything(query, count, offset=0, timeout=5):
    base_url = get_everything_url()
    params = urllib.parse.urlencode({
        "search": query,
        "offset": offset,
        "count": count,
        "json": 1,
        "path_column": 1,
        "name_column": 1,
    })
    url = "{}/?{}".format(base_url, params)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return (None, "Cannot connect to Everything at {}".format(base_url))
    except Exception as e:
        if "timed out" in str(e).lower():
            return (None, "Request timed out")
        return (None, "Error: {}".format(str(e)))

    results = []
    for item in data.get("results", []):
        path = item.get("path", "")
        name = item.get("name", "")
        if path and name:
            full_path = os.path.join(path, name)
            results.append(full_path)

    return (results, None)


# --- File Display Logic ---

def filter_and_format_results(file_paths):
    blacklist = get_setting("blacklist")
    blacklist_set = {ext.lower() for ext in blacklist}

    filtered = []
    for fp in file_paths:
        ext = os.path.splitext(fp)[1].lower()
        if ext in blacklist_set:
            continue
        if ext in KNOWN_BINARY_EXTENSIONS:
            filtered.append(("[binary] " + fp, fp))
        else:
            filtered.append((fp, fp))

    return filtered


# --- Search Logic ---

def perform_search(prefix, query):
    query_text = query.strip()
    if not query_text:
        return ([], None)

    if prefix and query_text.startswith(prefix):
        user_part = query_text[len(prefix):]
    else:
        user_part = query_text

    if not user_part.strip():
        return ([], None)

    search_query = build_query(user_part, prefix=prefix)
    max_results = get_setting("max_results")
    results, error = search_everything(search_query, count=max_results)

    if error:
        return ([], error)

    formatted = filter_and_format_results(results) if results else []
    return (formatted, None)


# --- Input Handlers ---

class EverythingSearchQueryHandler(sublime_plugin.TextInputHandler):

    def __init__(self, prefix):
        self._prefix = prefix

    def name(self):
        return "query"

    def placeholder(self):
        if self._prefix:
            return "Enter {} query, press Enter to search".format(self._prefix.rstrip(":"))
        return "Enter search query, press Enter (config:key=value to configure)"

    def initial_text(self):
        return _last_query.get(self._prefix, self._prefix)

    def next_input(self, args):
        query = args.get("query", "").strip()
        if not query or query.startswith("config:"):
            return None

        _last_query[self._prefix] = query

        formatted, error = perform_search(self._prefix, query)

        if error:
            return EverythingSearchResultHandler(self._prefix, query, [], error, 0, False)

        if not formatted:
            return EverythingSearchResultHandler(self._prefix, query, [], "No results found", 0, False)

        return EverythingSearchResultHandler(self._prefix, query, formatted, None, 0, False)


class EverythingSearchResultHandler(sublime_plugin.ListInputHandler):

    def __init__(self, prefix, query, formatted, error, page, alt):
        self._prefix = prefix
        self._query = query
        self._formatted = formatted
        self._error = error
        self._page = page
        self._alt = alt
        per_page = get_setting("results_per_page")
        self._per_page = per_page
        self._total = len(formatted)
        self._total_pages = max(1, (self._total + per_page - 1) // per_page)

    def name(self):
        return "selected_path_pg" if self._alt else "selected_path"

    def placeholder(self):
        if self._error:
            return "EverythingSearch: {} (Backspace to modify query)".format(self._error)
        return "Page {}/{} ({} results) for: {}".format(
            self._page + 1, self._total_pages, self._total, self._query
        )

    def list_items(self):
        if self._error:
            return [("[ {} ]".format(self._error), "__noop__")]

        items = []
        start = self._page * self._per_page
        end = min(start + self._per_page, self._total)

        if self._page > 0:
            items.append(("<< Previous Page ({}/{})".format(self._page, self._total_pages), "__prev__"))

        for display, path in self._formatted[start:end]:
            items.append((display, path))

        if end < self._total:
            items.append(("Next Page >> ({}/{})".format(self._page + 2, self._total_pages), "__next__"))

        return items

    def description(self, value, text):
        if value in ("__next__", "__prev__", "__noop__"):
            return ""
        return text

    def next_input(self, args):
        value = args.get(self.name(), "")
        if value == "__next__":
            return EverythingSearchResultHandler(
                self._prefix, self._query, self._formatted, None,
                self._page + 1, not self._alt
            )
        if value == "__prev__":
            return EverythingSearchResultHandler(
                self._prefix, self._query, self._formatted, None,
                max(0, self._page - 1), not self._alt
            )
        return None


# --- Command ---

_NAV_VALUES = ("__next__", "__prev__", "__noop__", "")


class EverythingSearchCommand(sublime_plugin.WindowCommand):

    def run(self, prefix="", query="", selected_path="", selected_path_pg=""):
        if query.startswith("config:"):
            msg = apply_config_command(query)
            sublime.status_message(msg)
            return

        path = ""
        if selected_path not in _NAV_VALUES:
            path = selected_path
        elif selected_path_pg not in _NAV_VALUES:
            path = selected_path_pg

        if path:
            self.window.open_file(path)

    def input(self, args):
        if "query" not in args:
            return EverythingSearchQueryHandler(args.get("prefix", ""))
        return None
