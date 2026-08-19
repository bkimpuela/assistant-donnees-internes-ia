"""validation des requêtes SQL écrites par le LLM.

le role PostgreSQL read-only est la vraie protection. ce module est une
deuxième barrière, placée avant l'exécution : je rejette tôt ce qui n'a aucune
raison d'être généré, et je transforme le refus en quelque chose que je peux
expliquer au visiteur.

chaque refus porte un `code` court plutôt qu'une phrase toute faite. l'API
transforme ce code en message dans la langue du visiteur (voir `app/i18n.py`),
comme ça ce module ne contient aucun texte traduit.
"""

import re

# seules ces tables ont le droit d'apparaître dans une requête générée.
ALLOWED_TABLES = {
    "film",
    "person",
    "genre",
    "country",
    "film_genre",
    "film_country",
    "film_director",
    "film_cast",
}

# mots-clés qui n'ont aucune raison d'apparaître dans une requête de lecture.
FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "copy", "vacuum", "reindex", "cluster",
    "pg_read_file", "pg_sleep", "dblink", "lo_import", "lo_export",
}

# repère les noms de tables cités après FROM ou JOIN.
_TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_COMMENT_PATTERN = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)

# certaines fonctions SQL utilisent le mot-clé FROM sans désigner de table :
# EXTRACT(YEAR FROM release_date), SUBSTRING(label FROM 1 FOR 3), TRIM(... FROM ...).
# je les neutralise avant d'aller chercher les noms de tables, sinon je prendrais
# « release_date » pour une table.
_FROM_FUNCTION_PATTERN = re.compile(
    r"\b(?:extract|substring|trim|position|overlay)\s*\([^()]*\)",
    re.IGNORECASE,
)

# les string literals contiennent de la donnée, pas du code : un film intitulé
# « The Update » ne doit pas déclencher le mot-clé interdit `update`.
_STRING_LITERAL_PATTERN = re.compile(r"'(?:[^']|'')*'")


class UnsafeQueryError(ValueError):
    """levée quand une requête générée ne respecte pas une des règles.

    `code` identifie la règle enfreinte, pour que l'API puisse formuler le refus
    dans la langue du visiteur. `subject` porte la valeur fautive quand il y en a
    une : un nom de table, un mot-clé.
    """

    def __init__(self, code: str, subject: str = "") -> None:
        self.code = code
        self.subject = subject
        super().__init__(f"{code}: {subject}" if subject else code)


def _strip_comments(sql: str) -> str:
    """retire les commentaires SQL, qui peuvent servir à cacher du code."""
    return _COMMENT_PATTERN.sub(" ", sql)


def _mask_string_literals(sql: str) -> str:
    """vide le contenu des string literals, en gardant les quotes.

    mon analyse porte alors sur la structure de la requête, jamais sur la donnée
    recherchée. je conserve la longueur d'origine pour ne pas décaler les
    positions des caractères.
    """
    return _STRING_LITERAL_PATTERN.sub(lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", sql)


def validate_sql(sql: str) -> str:
    """valide la requête et renvoie la version normalisée, prête à exécuter.

    lève UnsafeQueryError si une des règles n'est pas respectée.
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("empty_query")

    cleaned = _strip_comments(sql).strip().rstrip(";").strip()

    # je fais toutes mes vérifications sur la version masquée : le contenu des
    # string literals est de la donnée, pas du code.
    masked = _mask_string_literals(cleaned)
    lowered = masked.lower()

    # règle 1 : une seule instruction. un ';' qui reste signale un enchaînement.
    if ";" in masked:
        raise UnsafeQueryError("multiple_statements")

    # règle 2 : la requête doit commencer par SELECT, ou par une CTE WITH ... SELECT.
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise UnsafeQueryError("not_a_select")

    # règle 3 : aucun mot-clé de modification ou d'accès système.
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            raise UnsafeQueryError("forbidden_keyword", keyword)

    # règle 4 : aucun accès aux schemas internes de PostgreSQL.
    if "pg_catalog" in lowered or "information_schema" in lowered or "pg_" in lowered:
        raise UnsafeQueryError("system_tables")

    # règle 5 : chaque table citée appartient à l'allow-list. je tolère les alias
    # de CTE, puisqu'ils sont définis à l'intérieur de la requête elle-même.
    cte_names = {
        name.lower()
        for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", masked, re.IGNORECASE)
    }
    scannable = _FROM_FUNCTION_PATTERN.sub(" ", masked)
    for table in _TABLE_PATTERN.findall(scannable):
        name = table.lower()
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise UnsafeQueryError("table_not_allowed", table)

    return cleaned


def enforce_limit(sql: str, max_rows: int) -> str:
    """ajoute un LIMIT quand la requête n'en a pas, ou le plafonne s'il est trop haut."""
    match = re.search(r"\blimit\s+(\d+)\s*$", sql, re.IGNORECASE)
    if match is None:
        return f"{sql} LIMIT {max_rows}"

    requested = int(match.group(1))
    if requested > max_rows:
        return sql[: match.start()] + f"LIMIT {max_rows}"
    return sql
