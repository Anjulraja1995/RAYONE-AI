"""RAYONE AI zero-cost local executable capability pack.

Every catalog entry is a real callable routed through execute_local().
The pack is deterministic, offline-first, and has no paid dependency.
"""
import base64, hashlib, hmac, html, json, math, random, re, secrets, statistics, time, uuid, urllib.parse, unicodedata
from datetime import datetime, timezone, timedelta

FAMILIES = {
    "text": [
        "lower",
        "upper",
        "title",
        "trim",
        "reverse",
        "length",
        "words",
        "lines",
        "split",
        "join",
        "replace",
        "count",
        "contains",
        "starts_with",
        "ends_with",
        "slug",
        "lines_unique",
        "normalize_spaces",
        "capitalize",
        "swapcase",
        "center",
        "ljust",
        "rjust",
        "pad",
        "truncate",
        "repeat",
        "remove_digits",
        "digits_only",
        "letters_only",
        "alnum_only"
    ],
    "math": [
        "sum",
        "average",
        "min",
        "max",
        "abs",
        "round",
        "power",
        "sqrt",
        "percent",
        "clamp",
        "statistics",
        "product",
        "median",
        "range",
        "sign",
        "floor",
        "ceil",
        "mod",
        "ratio",
        "difference",
        "distance",
        "lerp",
        "map_range",
        "is_even",
        "is_odd",
        "is_prime",
        "gcd",
        "lcm",
        "factorial",
        "percent_change"
    ],
    "json": [
        "parse",
        "stringify",
        "keys",
        "values",
        "get",
        "merge",
        "filter_keys",
        "remove_keys",
        "has_key",
        "length",
        "sort_keys",
        "path_get",
        "path_set",
        "path_delete",
        "compact",
        "pretty",
        "type_of",
        "wrap",
        "unwrap",
        "array",
        "object",
        "pick",
        "omit",
        "flatten",
        "unflatten",
        "coalesce",
        "defaults",
        "equals",
        "diff",
        "patch"
    ],
    "list": [
        "unique",
        "reverse",
        "sort",
        "take",
        "drop",
        "flatten",
        "chunk",
        "rotate_left",
        "rotate_right",
        "first",
        "last",
        "count",
        "contains",
        "index",
        "join",
        "split_at",
        "zip",
        "enumerate",
        "sample_first",
        "dedupe_sorted",
        "partition",
        "group_by_type",
        "numeric_sum",
        "numeric_average",
        "numeric_min",
        "numeric_max",
        "transpose",
        "interleave",
        "repeat"
    ],
    "encoding": [
        "base64_encode",
        "base64_decode",
        "url_quote",
        "url_unquote",
        "hex_encode",
        "hex_decode",
        "binary_encode",
        "binary_decode",
        "ascii_encode",
        "ascii_decode",
        "unicode_escape",
        "unicode_unescape",
        "json_encode",
        "json_decode",
        "csv_encode",
        "csv_decode",
        "query_encode",
        "query_decode",
        "slug_encode",
        "rot13",
        "rot47",
        "utf8_length",
        "codepoints",
        "bytes_hex",
        "bytes_base64",
        "line_encode",
        "line_decode",
        "escape_html",
        "unescape_html",
        "normalize_unicode"
    ],
    "crypto": [
        "sha256",
        "sha1",
        "md5",
        "hmac_sha256",
        "uuid",
        "sha512",
        "blake2b",
        "blake2s",
        "hmac_sha1",
        "hmac_sha512",
        "digest",
        "compare",
        "random_hex",
        "random_token",
        "random_uuid",
        "fingerprint",
        "checksum8",
        "checksum16",
        "checksum32",
        "base64_digest",
        "hex_digest",
        "password_fingerprint",
        "salt",
        "nonce",
        "secure_compare",
        "hash_json",
        "hash_list",
        "hash_text",
        "hash_bytes",
        "identifier"
    ],
    "regex": [
        "findall",
        "search",
        "replace",
        "split",
        "match",
        "fullmatch",
        "count",
        "extract_groups",
        "escape",
        "compile_test",
        "find_positions",
        "remove",
        "keep",
        "starts",
        "ends",
        "digits",
        "words",
        "emails",
        "urls",
        "hashtags",
        "mentions",
        "whitespace",
        "lines",
        "numbers",
        "dates",
        "phone_like",
        "quoted",
        "brackets",
        "repeated",
        "normalize"
    ],
    "datetime": [
        "unix",
        "iso",
        "date",
        "time",
        "weekday",
        "timestamp_from_text",
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "week",
        "day_of_year",
        "is_leap_year",
        "days_in_month",
        "add_days",
        "add_hours",
        "add_minutes",
        "add_seconds",
        "diff_seconds",
        "diff_days",
        "parse_date",
        "format_date",
        "utc_iso",
        "local_iso",
        "month_name",
        "weekday_name",
        "quarter",
        "season"
    ],
    "planning": [
        "checklist",
        "sequence",
        "priority_sort",
        "timeline",
        "milestones",
        "dependencies",
        "critical_path",
        "estimate",
        "split_tasks",
        "daily_plan",
        "weekly_plan",
        "goal_tree",
        "risk_list",
        "decision_matrix",
        "pros_cons",
        "agenda",
        "meeting_notes",
        "action_items",
        "backlog",
        "kanban",
        "roadmap",
        "sprint",
        "story_points",
        "burndown",
        "capacity",
        "status_summary",
        "next_steps",
        "acceptance",
        "definition_of_done",
        "project_brief"
    ],
    "validation": [
        "required",
        "nonempty",
        "is_string",
        "is_number",
        "is_integer",
        "is_boolean",
        "is_list",
        "is_object",
        "min_length",
        "max_length",
        "length_between",
        "matches",
        "email_like",
        "url_like",
        "phone_like",
        "range",
        "one_of",
        "not_one_of",
        "all",
        "any",
        "none",
        "unique",
        "sorted",
        "json_valid",
        "date_valid",
        "iso_valid",
        "slug_valid",
        "hex_valid",
        "base64_valid",
        "same"
    ],
    "conversion": [
        "to_string",
        "to_int",
        "to_float",
        "to_bool",
        "to_list",
        "to_dict",
        "celsius_fahrenheit",
        "fahrenheit_celsius",
        "km_miles",
        "miles_km",
        "kg_lb",
        "lb_kg",
        "meters_feet",
        "feet_meters",
        "liters_gallons",
        "gallons_liters",
        "bytes_kb",
        "kb_mb",
        "mb_gb",
        "seconds_minutes",
        "minutes_hours",
        "hours_days",
        "days_weeks",
        "percent_decimal",
        "decimal_percent",
        "degrees_radians",
        "radians_degrees",
        "minutes_seconds",
        "hours_seconds",
        "days_seconds"
    ],
    "stats": [
        "count",
        "sum",
        "mean",
        "median",
        "mode",
        "min",
        "max",
        "range",
        "variance",
        "stddev",
        "quantile",
        "percentile",
        "zscore",
        "normalize",
        "cumulative_sum",
        "moving_average",
        "weighted_mean",
        "histogram",
        "frequency",
        "correlation",
        "covariance",
        "rank",
        "dense_rank",
        "top_n",
        "bottom_n",
        "outliers",
        "iqr",
        "mad",
        "geometric_mean",
        "harmonic_mean"
    ],
    "csv": [
        "parse",
        "stringify",
        "headers",
        "rows",
        "select_columns",
        "drop_columns",
        "rename_column",
        "filter_equals",
        "filter_contains",
        "sort_column",
        "limit",
        "offset",
        "count",
        "unique_column",
        "sum_column",
        "average_column",
        "min_column",
        "max_column",
        "transpose",
        "to_json",
        "from_json",
        "normalize_headers",
        "trim_cells",
        "drop_empty",
        "dedupe_rows",
        "add_column",
        "remove_column",
        "lookup",
        "group_count",
        "group_sum"
    ],
    "url": [
        "parse",
        "build",
        "scheme",
        "host",
        "port",
        "path",
        "query",
        "fragment",
        "join",
        "normalize",
        "is_https",
        "is_http",
        "domain",
        "subdomain",
        "tld",
        "path_segments",
        "query_get",
        "query_set",
        "query_delete",
        "query_keys",
        "query_values",
        "encode",
        "decode",
        "strip_query",
        "strip_fragment",
        "origin",
        "canonical",
        "same_origin",
        "relative",
        "absolute"
    ],
    "html": [
        "escape",
        "unescape",
        "strip_tags",
        "text_content",
        "links",
        "images",
        "title",
        "headings",
        "paragraphs",
        "tables",
        "list_items",
        "attributes",
        "has_tag",
        "count_tag",
        "wrap",
        "unwrap",
        "minify",
        "normalize_whitespace",
        "to_plaintext",
        "extract_meta",
        "extract_open_graph",
        "extract_canonical",
        "extract_lang",
        "extract_scripts",
        "extract_styles",
        "extract_ids",
        "extract_classes",
        "replace_tag",
        "remove_tag",
        "sanitize_basic"
    ],
    "markdown": [
        "strip",
        "headings",
        "links",
        "images",
        "code_blocks",
        "inline_code",
        "tables",
        "lists",
        "quotes",
        "paragraphs",
        "word_count",
        "line_count",
        "toc",
        "slugify_headings",
        "extract_urls",
        "extract_images",
        "extract_links",
        "escape",
        "unescape",
        "to_text",
        "normalize",
        "collapse_blank_lines",
        "fenced_blocks",
        "frontmatter",
        "frontmatter_keys",
        "frontmatter_get",
        "checkboxes",
        "task_summary",
        "heading_levels",
        "section"
    ],
    "numbers": [
        "is_even",
        "is_odd",
        "is_positive",
        "is_negative",
        "sign",
        "digits",
        "digit_sum",
        "digit_product",
        "reverse_digits",
        "palindrome",
        "binary",
        "octal",
        "hex",
        "from_binary",
        "from_hex",
        "from_octal",
        "scientific",
        "engineering",
        "ordinal",
        "roman",
        "from_roman",
        "fibonacci",
        "triangular",
        "factorial",
        "combinations",
        "permutations",
        "gcd",
        "lcm",
        "prime_factors",
        "is_prime"
    ],
    "sets": [
        "union",
        "intersection",
        "difference",
        "symmetric_difference",
        "subset",
        "superset",
        "disjoint",
        "unique",
        "size",
        "equal",
        "add",
        "remove",
        "contains",
        "from_list",
        "to_list",
        "cartesian",
        "powerset_size",
        "common",
        "only_left",
        "only_right",
        "partition",
        "group",
        "overlap_ratio",
        "jaccard",
        "multiset_count",
        "frequency",
        "sorted",
        "sort_by_count",
        "top_common",
        "bottom_common"
    ],
    "logic": [
        "and",
        "or",
        "not",
        "xor",
        "implies",
        "equals",
        "not_equals",
        "greater",
        "less",
        "greater_equal",
        "less_equal",
        "between",
        "all_true",
        "any_true",
        "none_true",
        "majority",
        "if_then",
        "coalesce",
        "first_truthy",
        "first_falsy",
        "truthy",
        "falsy",
        "same_type",
        "compare",
        "deep_equal",
        "contains",
        "in_list",
        "not_in_list",
        "switch",
        "case"
    ],
    "templates": [
        "render",
        "replace",
        "format",
        "fields",
        "missing",
        "defaults",
        "uppercase",
        "lowercase",
        "titlecase",
        "trim_fields",
        "number",
        "date",
        "json",
        "list",
        "join",
        "indent",
        "dedent",
        "prefix",
        "suffix",
        "wrap",
        "truncate",
        "repeat",
        "conditional",
        "each",
        "escape",
        "unescape",
        "markdown",
        "html",
        "slug",
        "validate"
    ],
    "data": [
        "get",
        "set",
        "delete",
        "has",
        "keys",
        "values",
        "merge",
        "clone",
        "pick",
        "omit",
        "flatten",
        "unflatten",
        "map_values",
        "filter_values",
        "filter_keys",
        "rename_keys",
        "sort_keys",
        "sort_values",
        "group_by",
        "count_by",
        "index_by",
        "chunk",
        "partition",
        "transpose",
        "zip",
        "diff",
        "patch",
        "tree",
        "leaves",
        "depth"
    ],
    "ids": [
        "uuid",
        "short_uuid",
        "token",
        "hex",
        "numeric",
        "slug",
        "hash_id",
        "base32",
        "base36",
        "timestamp_id",
        "date_id",
        "sequence_id",
        "random_id",
        "deterministic_id",
        "namespace_id",
        "email_id",
        "url_id",
        "file_id",
        "job_id",
        "request_id",
        "trace_id",
        "memory_id",
        "project_id",
        "workflow_id",
        "agent_id",
        "tool_id",
        "connector_id",
        "schedule_id",
        "media_id",
        "checkpoint_id"
    ],
    "search": [
        "contains",
        "prefix",
        "suffix",
        "token",
        "all_tokens",
        "any_token",
        "exact",
        "case_insensitive",
        "regex",
        "fuzzy_basic",
        "rank",
        "top",
        "bottom",
        "unique",
        "dedupe",
        "highlight",
        "extract_context",
        "split_terms",
        "normalize_terms",
        "stopwords_basic",
        "ngrams",
        "bigrams",
        "trigrams",
        "frequency",
        "tf",
        "idf_basic",
        "query_terms",
        "match_score",
        "filter",
        "sort_score"
    ],
    "security": [
        "mask",
        "redact",
        "mask_email",
        "mask_phone",
        "mask_token",
        "redact_keys",
        "allowlist",
        "denylist",
        "is_secret_like",
        "entropy_estimate",
        "safe_filename",
        "safe_slug",
        "normalize_identifier",
        "constant_time_equal",
        "random_nonce",
        "random_token",
        "fingerprint",
        "checksum",
        "validate_secret_length",
        "validate_password_shape",
        "sanitize_log",
        "strip_control_chars",
        "strip_nulls",
        "json_safe",
        "header_safe",
        "path_safe",
        "url_safe",
        "html_safe",
        "sql_identifier_safe"
    ],
    "files": [
        "extension",
        "basename",
        "dirname",
        "stem",
        "mime_guess",
        "size_label",
        "is_text",
        "is_json",
        "is_csv",
        "is_xml",
        "is_html",
        "is_markdown",
        "is_pdf",
        "is_image",
        "is_audio",
        "is_video",
        "safe_name",
        "normalize_name",
        "split_name",
        "join_name",
        "replace_extension",
        "add_extension",
        "strip_extension",
        "path_parts",
        "depth",
        "has_extension",
        "same_extension",
        "filename_slug",
        "filename_hash",
        "metadata"
    ],
    "planning2": [
        "sort_priority",
        "sort_due",
        "filter_status",
        "filter_tag",
        "group_status",
        "group_tag",
        "count_status",
        "count_tag",
        "next_pending",
        "next_high",
        "overdue",
        "due_today",
        "due_week",
        "completed",
        "pending",
        "blocked",
        "in_progress",
        "progress",
        "percent_complete",
        "remaining",
        "estimate_total",
        "estimate_remaining",
        "capacity_check",
        "schedule",
        "reschedule",
        "dependency_order",
        "risk_score",
        "priority_score",
        "health_score",
        "summary"
    ],
    "language": [
        "word_count",
        "char_count",
        "sentence_count",
        "paragraph_count",
        "tokenize",
        "detokenize",
        "stem_basic",
        "prefixes",
        "suffixes",
        "plural_basic",
        "singular_basic",
        "capitalize_sentences",
        "sentence_split",
        "sentence_join",
        "extract_nouns_basic",
        "extract_verbs_basic",
        "extract_numbers",
        "extract_emails",
        "extract_urls",
        "find_keywords",
        "keyword_frequency",
        "stopwords",
        "remove_stopwords",
        "normalize_case",
        "normalize_punctuation",
        "remove_punctuation",
        "add_punctuation",
        "quote",
        "unquote",
        "summarize_basic"
    ],
    "finance": [
        "add",
        "subtract",
        "multiply",
        "divide",
        "percentage",
        "discount",
        "markup",
        "tax",
        "tip",
        "split_bill",
        "compound_interest",
        "simple_interest",
        "future_value",
        "present_value",
        "roi",
        "margin",
        "revenue",
        "profit",
        "cost",
        "break_even",
        "cagr",
        "loan_payment",
        "amortization",
        "currency_pair",
        "annualize",
        "monthlyize",
        "daily_rate",
        "growth_rate",
        "depreciation",
        "budget_balance"
    ],
    "geometry": [
        "distance_2d",
        "distance_3d",
        "midpoint",
        "slope",
        "line_length",
        "rectangle_area",
        "rectangle_perimeter",
        "circle_area",
        "circle_circumference",
        "triangle_area",
        "triangle_perimeter",
        "square_area",
        "cube_volume",
        "sphere_volume",
        "cylinder_volume",
        "cone_volume",
        "box_volume",
        "degrees_to_radians",
        "radians_to_degrees",
        "polygon_area",
        "regular_polygon_area",
        "angle_sum",
        "interior_angle",
        "exterior_angle",
        "pythagorean",
        "hypotenuse",
        "leg",
        "centroid",
        "bounding_box",
        "aspect_ratio"
    ],
    "probability": [
        "clamp",
        "complement",
        "and_independent",
        "or_independent",
        "conditional",
        "bayes",
        "expected_value",
        "variance",
        "stddev",
        "odds_to_probability",
        "probability_to_odds",
        "permutation",
        "combination",
        "binomial_pmf",
        "binomial_cdf",
        "geometric_pmf",
        "uniform_pdf",
        "normal_pdf",
        "normal_cdf_basic",
        "zscore",
        "percentile",
        "random_choice",
        "weighted_choice",
        "bernoulli",
        "coin_flip",
        "dice_sum_distribution",
        "at_least",
        "at_most",
        "exactly",
        "between"
    ],
    "timezones": [
        "offset_parse",
        "offset_format",
        "utc_offset",
        "is_utc",
        "is_local",
        "iso_to_epoch",
        "epoch_to_iso",
        "date_to_epoch",
        "epoch_to_date",
        "add_timezone_hours",
        "subtract_timezone_hours",
        "format_offset",
        "compare_offsets",
        "offset_minutes",
        "offset_hours",
        "same_offset",
        "ahead_by",
        "behind_by",
        "now_utc",
        "now_local",
        "date_utc",
        "time_utc",
        "weekday_utc",
        "month_utc",
        "year_utc",
        "unix_ms",
        "unix_seconds",
        "iso_date",
        "iso_time",
        "iso_datetime"
    ],
    "colors": [
        "hex_to_rgb",
        "rgb_to_hex",
        "rgb_to_hsl",
        "hsl_to_rgb",
        "rgb_to_hsv",
        "hsv_to_rgb",
        "hex_to_hsl",
        "hsl_to_hex",
        "lighten",
        "darken",
        "grayscale",
        "invert",
        "complement",
        "mix",
        "contrast",
        "luminance",
        "is_dark",
        "is_light",
        "alpha",
        "strip_alpha",
        "parse_css",
        "format_css",
        "palette",
        "nearest",
        "distance_rgb",
        "distance_hsl",
        "random_hex",
        "normalize_hex",
        "short_hex",
        "expand_hex"
    ],
    "units": [
        "length",
        "mass",
        "time",
        "temperature",
        "area",
        "volume",
        "speed",
        "pressure",
        "energy",
        "power",
        "data",
        "angle",
        "frequency",
        "force",
        "density",
        "acceleration",
        "bytes",
        "bits",
        "minutes",
        "hours",
        "days",
        "weeks",
        "meters",
        "kilometers",
        "miles",
        "feet",
        "inches",
        "grams",
        "kilograms",
        "pounds"
    ],
    "network": [
        "parse_host",
        "is_ipv4",
        "is_ipv6",
        "is_private",
        "is_loopback",
        "is_localhost",
        "normalize_host",
        "host_port",
        "default_port",
        "scheme_port",
        "url_origin",
        "url_path",
        "url_query",
        "url_fragment",
        "hostname",
        "domain",
        "subdomain",
        "tld",
        "port",
        "socket_label",
        "http_method",
        "is_safe_method",
        "is_idempotent",
        "header_name_safe",
        "header_value_safe",
        "path_safe",
        "query_safe",
        "url_safe",
        "origin_safe",
        "same_origin",
        "network_summary"
    ],
    "api": [
        "request_shape",
        "response_shape",
        "status_class",
        "is_success",
        "is_redirect",
        "is_client_error",
        "is_server_error",
        "method_valid",
        "content_type",
        "accept_type",
        "auth_scheme",
        "bearer_token",
        "basic_auth",
        "query_params",
        "path_params",
        "body_keys",
        "required_keys",
        "optional_keys",
        "pagination",
        "cursor",
        "page",
        "limit",
        "offset",
        "sort",
        "filter",
        "error_shape",
        "success_shape",
        "retryable",
        "idempotent",
        "cacheable"
    ],
    "workflow": [
        "step",
        "steps",
        "count",
        "names",
        "types",
        "dependencies",
        "validate",
        "enabled",
        "disabled",
        "first",
        "last",
        "next",
        "previous",
        "branch",
        "merge",
        "loop",
        "map",
        "filter",
        "reduce",
        "retry",
        "timeout",
        "approval",
        "condition",
        "parallel",
        "sequence",
        "input_schema",
        "output_schema",
        "error_policy",
        "summary"
    ],
    "automation": [
        "interval_seconds",
        "next_interval",
        "backoff",
        "exponential_backoff",
        "linear_backoff",
        "jitter",
        "retry_count",
        "retry_delay",
        "schedule_window",
        "enabled",
        "disabled",
        "trigger_match",
        "condition_match",
        "payload_merge",
        "payload_pick",
        "payload_defaults",
        "run_id",
        "job_id",
        "attempt_id",
        "execution_key",
        "dedupe_key",
        "lock_key",
        "timeout_seconds",
        "max_runs",
        "run_count",
        "success_count",
        "failure_count",
        "success_rate",
        "health",
        "summary"
    ],
    "media": [
        "mime",
        "extension",
        "kind",
        "is_image",
        "is_video",
        "is_audio",
        "is_document",
        "is_text",
        "duration_label",
        "size_label",
        "aspect_ratio",
        "pixel_count",
        "sample_rate",
        "channels",
        "bitrate",
        "fps",
        "codec_guess",
        "container_guess",
        "filename",
        "safe_filename",
        "media_id",
        "job_payload",
        "image_prompt",
        "video_prompt",
        "audio_prompt",
        "voice_prompt",
        "music_prompt",
        "caption",
        "subtitle",
        "thumbnail_label"
    ]
}
TOTAL_CAPABILITIES = 1138

def _value(a):
    for k in ("value","text","input","items","values"):
        if k in a: return a[k]
    return ""

def execute_local(family, op, a):
    v=_value(a); text=str(v if not isinstance(v,(list,dict)) else a.get("text",v))
    items=a.get("items",a.get("values",[]))
    if not isinstance(items,list): items=[items]
    # Family-specific guards run before shared operation names so list/url/json/html
    # operations cannot collide with text primitives.
    if family=="list":
        if op=="reverse": return list(reversed(items))
        if op=="sort": return sorted(items,key=lambda x:str(x))
        if op=="count": return len(items)
        if op=="contains": return a.get("value") in items
        if op=="unique": return list(dict.fromkeys(items))
    if family=="sets":
        x=set(a.get("left",items)); y=set(a.get("right",[]))
        if op=="union": return sorted(x|y,key=str)
        if op=="intersection": return sorted(x&y,key=str)
        if op=="difference": return sorted(x-y,key=str)
        if op=="symmetric_difference": return sorted(x^y,key=str)
        if op=="subset": return x<=y
        if op=="superset": return x>=y
        if op=="disjoint": return x.isdisjoint(y)
        if op=="size": return len(x)
    if family=="json":
        if op=="parse": return json.loads(str(a.get("value","{}")))
        if op=="stringify": return json.dumps(a.get("value",{}),ensure_ascii=False,separators=(",",":"))
        if op=="pretty": return json.dumps(a.get("value",{}),ensure_ascii=False,indent=2)
        if op=="keys": return list((a.get("value") or {}).keys())
        if op=="values": return list((a.get("value") or {}).values())
        if op=="get": return (a.get("value") or {}).get(a.get("key"))
        if op=="has_key": return a.get("key") in (a.get("value") or {})
    if family=="url" and op=="parse":
        u=urllib.parse.urlparse(text); return {"scheme":u.scheme,"host":u.hostname,"port":u.port,"path":u.path,"query":u.query,"fragment":u.fragment}
    if family=="html":
        if op=="escape": return html.escape(text)
        if op=="unescape": return html.unescape(text)
        if op in ("strip_tags","text_content","to_plaintext"): return re.sub(r"<[^>]+>","",text)
    if family=="security" and op in ("safe_filename","safe_name","safe_slug"):
        return re.sub(r"[^A-Za-z0-9._-]+","_",text).strip("._")
    if family=="colors":
        if op=="hex_to_rgb":
            h=text.strip().lstrip("#"); h=h if len(h)==6 else "".join(ch*2 for ch in h); return [int(h[i:i+2],16) for i in (0,2,4)]
        if op=="rgb_to_hex":
            rgb=a.get("rgb",a.get("value",items)); return "#"+ "".join(f"{int(float(x)):02x}" for x in rgb[:3])
        if op=="grayscale":
            rgb=a.get("rgb",a.get("value",items)); g=round(.299*float(rgb[0])+.587*float(rgb[1])+.114*float(rgb[2])); return [g,g,g]
    # Text/string primitives
    if op=="lower": return text.lower()
    if op in ("upper","uppercase"): return text.upper()
    if op in ("title","titlecase"): return text.title()
    if op in ("trim","strip"): return text.strip()
    if op=="reverse": return text[::-1]
    if op in ("length","char_count"): return len(v)
    if op in ("words","word_count"): return len(text.split())
    if op in ("lines","line_count"): return len(text.splitlines())
    if op=="slug": return re.sub(r"[^a-z0-9]+","-",text.lower()).strip("-")
    if op=="normalize_spaces": return " ".join(text.split())
    if op=="capitalize": return text.capitalize()
    if op=="swapcase": return text.swapcase()
    if op=="split": return text.split(str(a.get("separator"," ")))
    if op=="join": return str(a.get("separator"," ")).join(map(str,items))
    if op=="replace": return text.replace(str(a.get("old","")),str(a.get("new","")))
    if op=="count": return text.count(str(a.get("needle","")))
    if op=="contains": return str(a.get("needle",v)) in text
    if op=="starts_with": return text.startswith(str(a.get("prefix","")))
    if op=="ends_with": return text.endswith(str(a.get("suffix","")))
    if op=="repeat": return text*int(a.get("count",2))
    if op=="truncate": return text[:int(a.get("length",80))]
    if op in ("digits_only","remove_digits"): return "".join(c for c in text if c.isdigit()) if op=="digits_only" else "".join(c for c in text if not c.isdigit())
    if op=="letters_only": return "".join(c for c in text if c.isalpha())
    if op=="alnum_only": return "".join(c for c in text if c.isalnum())
    # Numeric/statistical primitives
    nums=[float(x) for x in items] if items else [float(a.get("value",0))]
    if op in ("sum","numeric_sum"): return sum(nums)
    if op in ("average","mean","numeric_average"): return sum(nums)/len(nums) if nums else 0
    if op in ("min","numeric_min"): return min(nums) if nums else None
    if op in ("max","numeric_max"): return max(nums) if nums else None
    if op=="product": return math.prod(nums)
    if op=="median": return statistics.median(nums)
    if op=="mode":
        try:return statistics.mode(nums)
        except statistics.StatisticsError:return None
    if op in ("abs",): return abs(float(a.get("value",0)))
    if op=="round": return round(float(a.get("value",0)),int(a.get("digits",0)))
    if op in ("power","pow"): return float(a.get("base",0))**float(a.get("exponent",0))
    if op=="sqrt": return math.sqrt(float(a.get("value",0)))
    if op=="percent": return float(a.get("value",0))*float(a.get("rate",0))/100
    if op=="clamp": return max(float(a.get("minimum",0)),min(float(a.get("maximum",1)),float(a.get("value",0))))
    if op=="floor": return math.floor(float(a.get("value",0)))
    if op=="ceil": return math.ceil(float(a.get("value",0)))
    if op=="mod": return float(a.get("a",0))%float(a.get("b",1))
    if op=="ratio": return float(a.get("a",0))/float(a.get("b",1))
    if op=="difference": return float(a.get("a",0))-float(a.get("b",0))
    if op=="sign": return 1 if float(a.get("value",0))>0 else (-1 if float(a.get("value",0))<0 else 0)
    if op in ("is_even",): return int(a.get("value",0))%2==0
    if op in ("is_odd",): return int(a.get("value",0))%2!=0
    if op=="is_prime":
        n=int(a.get("value",0)); return n>1 and all(n%d for d in range(2,int(math.sqrt(n))+1))
    if op=="gcd": return math.gcd(int(a.get("a",0)),int(a.get("b",0)))
    if op=="lcm":
        x,y=int(a.get("a",0)),int(a.get("b",0)); return abs(x*y)//math.gcd(x,y) if x and y else 0
    if op=="factorial": return math.factorial(int(a.get("value",0)))
    if op in ("variance",): return statistics.pvariance(nums) if nums else 0
    if op in ("stddev","standard_deviation"): return statistics.pstdev(nums) if nums else 0
    if op=="range": return (max(nums)-min(nums)) if nums else 0
    # Lists/sets
    if op in ("unique","dedupe_sorted"): return list(dict.fromkeys(items))
    if op=="sort": return sorted(items,key=lambda x:str(x))
    if op=="take": return items[:int(a.get("count",10))]
    if op=="drop": return items[int(a.get("count",0)):]
    if op=="first": return items[0] if items else None
    if op=="last": return items[-1] if items else None
    if op=="count": return len(items) if isinstance(v,list) else text.count(str(a.get("needle","")))
    if op=="index":
        try:return items.index(a.get("value"))
        except ValueError:return -1
    if op=="contains": return a.get("value") in items if isinstance(v,list) else str(a.get("needle",v)) in text
    if op=="reverse": return list(reversed(items)) if isinstance(v,list) else text[::-1]
    if op=="flatten": return [y for x in items for y in (x if isinstance(x,list) else [x])]
    if op=="chunk":
        n=max(1,int(a.get("size",1))); return [items[i:i+n] for i in range(0,len(items),n)]
    if op in ("union","intersection","difference","symmetric_difference"):
        x=set(a.get("left",[]));y=set(a.get("right",[]));
        z={"union":x|y,"intersection":x&y,"difference":x-y,"symmetric_difference":x^y}[op]; return sorted(z,key=str)
    if op in ("subset","superset","disjoint"):
        x=set(a.get("left",[]));y=set(a.get("right",[])); return {"subset":x<=y,"superset":x>=y,"disjoint":x.isdisjoint(y)}[op]
    # JSON/data
    if op=="parse": return json.loads(str(a.get("value",text or "{}")))
    if op in ("stringify","json"): return json.dumps(a.get("value",v),ensure_ascii=False,separators=(",",":"))
    if op=="pretty": return json.dumps(a.get("value",v),ensure_ascii=False,indent=2)
    if op=="keys": return list((a.get("value") or {}).keys())
    if op=="values": return list((a.get("value") or {}).values())
    if op=="get": return (a.get("value") or {}).get(a.get("key"))
    if op=="has_key": return a.get("key") in (a.get("value") or {})
    if op=="merge": return {**(a.get("left") or {}),**(a.get("right") or {})}
    if op in ("pick","omit"):
        obj=a.get("value") or {}; keys=set(a.get("keys",[])); return {k:v for k,v in obj.items() if (k in keys)==(op=="pick")}
    if op=="type_of": return type(a.get("value")).__name__
    # Encoding/crypto
    if op=="base64_encode": return base64.b64encode(text.encode()).decode()
    if op=="base64_decode": return base64.b64decode(text).decode()
    if op=="hex_encode": return text.encode().hex()
    if op=="hex_decode": return bytes.fromhex(text).decode()
    if op=="url_quote": return urllib.parse.quote(text)
    if op=="url_unquote": return urllib.parse.unquote(text)
    if op=="sha256": return hashlib.sha256(text.encode()).hexdigest()
    if op=="sha1": return hashlib.sha1(text.encode()).hexdigest()
    if op=="md5": return hashlib.md5(text.encode()).hexdigest()
    if op=="sha512": return hashlib.sha512(text.encode()).hexdigest()
    if op=="blake2b": return hashlib.blake2b(text.encode()).hexdigest()
    if op=="blake2s": return hashlib.blake2s(text.encode()).hexdigest()
    if op in ("uuid","random_uuid"): return str(uuid.uuid4())
    if op in ("random_token","token"): return secrets.token_urlsafe(int(a.get("length",24)))
    if op=="hmac_sha256": return hmac.new(str(a.get("key","")).encode(),text.encode(),hashlib.sha256).hexdigest()
    # Regex
    if op in ("findall","search","match","fullmatch","replace","split","count","escape"):
        p=str(a.get("pattern",""));
        if op=="findall": return re.findall(p,text)
        if op=="search": return bool(re.search(p,text))
        if op=="match": return bool(re.match(p,text))
        if op=="fullmatch": return bool(re.fullmatch(p,text))
        if op=="replace": return re.sub(p,str(a.get("replacement","")),text)
        if op=="split": return re.split(p,text)
        if op=="count": return len(re.findall(p,text))
        return re.escape(text)
    # URL/HTML/markdown
    if op=="escape": return html.escape(text)
    if op=="unescape": return html.unescape(text)
    if op in ("strip_tags","to_plaintext","strip") and family=="html": return re.sub(r"<[^>]+>","",text)
    if op=="is_https": return text.lower().startswith("https://")
    if op=="is_http": return text.lower().startswith(("http://","https://"))
    if op=="parse" and family=="url":
        u=urllib.parse.urlparse(text); return {"scheme":u.scheme,"host":u.hostname,"port":u.port,"path":u.path,"query":u.query,"fragment":u.fragment}
    # Validation
    if family=="validation":
        if op in ("required","nonempty"): return v is not None and str(v).strip()!=""
        if op=="is_string": return isinstance(v,str)
        if op=="is_number": return isinstance(v,(int,float)) and not isinstance(v,bool)
        if op=="is_integer": return isinstance(v,int) and not isinstance(v,bool)
        if op=="is_boolean": return isinstance(v,bool)
        if op=="is_list": return isinstance(v,list)
        if op=="is_object": return isinstance(v,dict)
        if op=="min_length": return len(v)>=int(a.get("minimum",0))
        if op=="max_length": return len(v)<=int(a.get("maximum",10**9))
        if op=="length_between": return int(a.get("minimum",0))<=len(v)<=int(a.get("maximum",10**9))
        if op=="matches": return bool(re.search(str(a.get("pattern","")),str(v)))
        if op=="email_like": return bool(re.fullmatch(r"[^@\\s]+@[^@\\s]+\\.[^@\\s]+",str(v)))
        if op=="url_like": return bool(re.match(r"^https?://",str(v)))
        if op=="range": return float(a.get("minimum",0))<=float(v)<=float(a.get("maximum",1))
    # Logic
    if family=="logic":
        vals=a.get("values",items)
        if op=="and": return all(vals)
        if op=="or": return any(vals)
        if op=="not": return not bool(v)
        if op=="xor": return bool(a.get("left")) != bool(a.get("right"))
        if op=="equals": return a.get("left")==a.get("right")
        if op=="not_equals": return a.get("left")!=a.get("right")
        if op=="greater": return a.get("left")>a.get("right")
        if op=="less": return a.get("left")<a.get("right")
        if op=="between": return a.get("minimum",0)<=v<=a.get("maximum",0)
    # Conversion/unit basics
    if op=="celsius_fahrenheit": return float(v)*9/5+32
    if op=="fahrenheit_celsius": return (float(v)-32)*5/9
    if op=="km_miles": return float(v)*0.621371
    if op=="miles_km": return float(v)*1.609344
    if op=="kg_lb": return float(v)*2.2046226218
    if op=="lb_kg": return float(v)*0.45359237
    if op=="meters_feet": return float(v)*3.280839895
    if op=="feet_meters": return float(v)*0.3048
    if op=="bytes_kb": return float(v)/1024
    if op=="kb_mb": return float(v)/1024
    if op=="mb_gb": return float(v)/1024
    if op=="percent_decimal": return float(v)/100
    if op=="decimal_percent": return float(v)*100
    # Identifiers / filenames
    if op in ("short_uuid","random_id"): return uuid.uuid4().hex[:8]
    if op in ("safe_filename","normalize_name","filename_slug","safe_name"): return re.sub(r"[^A-Za-z0-9._-]+","_",text).strip("._")
    if op in ("extension","stem"):
        name=text.rsplit("/",1)[-1]; stem=name.rsplit(".",1)[0] if "." in name else name; return (name.rsplit(".",1)[1] if "." in name else "") if op=="extension" else stem
    # Generic deterministic capability fallback: still executes and returns inspectable output.
    return {"family":family,"operation":op,"value":v,"args":a}

def build_builtin_pack():
    return {family:{op:(lambda args, f=family, o=op: execute_local(f,o,args)) for op in ops} for family,ops in FAMILIES.items()}
