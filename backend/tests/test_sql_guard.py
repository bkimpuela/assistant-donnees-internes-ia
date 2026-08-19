"""tests du validator SQL.

c'est le code le plus sensible du projet : il décide si une requête écrite par
un LLM a le droit d'être exécutée. chaque test reproduit une tentative de
contournement réaliste.
"""

import pytest

from app.sql_guard import UnsafeQueryError, enforce_limit, validate_sql

# ---------------------------------------------------------------
# requêtes légitimes : elles doivent passer
# ---------------------------------------------------------------


def test_accepts_a_simple_select():
    sql = "SELECT qid, label FROM film WHERE release_year = 2010"
    assert validate_sql(sql) == sql


def test_accepts_a_join():
    sql = (
        "SELECT f.label, p.label FROM film f "
        "JOIN film_director fd ON fd.film_qid = f.qid "
        "JOIN person p ON p.qid = fd.person_qid"
    )
    assert validate_sql(sql) == sql


def test_accepts_a_cte():
    sql = (
        "WITH good_films AS (SELECT qid, label FROM film WHERE imdb_rating > 8) "
        "SELECT label FROM good_films"
    )
    assert validate_sql(sql) == sql


def test_accepts_extract_which_contains_the_word_from():
    # EXTRACT(YEAR FROM ...) contient FROM sans désigner de table. sans
    # précaution, je prendrais « release_date » pour une table interdite.
    sql = "SELECT EXTRACT(YEAR FROM release_date) AS year FROM film"
    assert validate_sql(sql) == sql


def test_strips_the_trailing_semicolon():
    assert validate_sql("SELECT label FROM film;") == "SELECT label FROM film"


def test_accepts_a_forbidden_keyword_inside_a_string_literal():
    # « The Update » est une donnée que je recherche, pas une instruction UPDATE.
    sql = "SELECT label FROM film WHERE label ILIKE '%The Update%'"
    assert validate_sql(sql) == sql


def test_accepts_a_semicolon_inside_a_string_literal():
    # même logique : ce ';' est dans une chaîne, ce n'est pas un enchaînement
    # d'instructions.
    sql = "SELECT label FROM film WHERE description ILIKE '%a; b%'"
    assert validate_sql(sql) == sql


# ---------------------------------------------------------------
# requêtes dangereuses : elles doivent être refusées
# ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM film",
        "DROP TABLE film",
        "UPDATE film SET label = 'x'",
        "INSERT INTO film (qid) VALUES ('Q1')",
        "TRUNCATE film",
        "ALTER TABLE film ADD COLUMN x TEXT",
        "GRANT ALL ON film TO public",
    ],
)
def test_rejects_modification_statements(sql):
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql)


def test_rejects_two_chained_statements():
    with pytest.raises(UnsafeQueryError):
        validate_sql("SELECT label FROM film; DROP TABLE film")


def test_neutralises_a_modification_hidden_in_a_block_comment():
    # je retire les commentaires avant analyse et je renvoie la requête nettoyée :
    # c'est cette version-là qui sera exécutée, donc le DROP n'atteint jamais
    # PostgreSQL. le neutraliser vaut mieux que le refuser.
    result = validate_sql("SELECT label FROM film /* ; DROP TABLE film */")
    assert "drop" not in result.lower()
    assert result.strip() == "SELECT label FROM film"


def test_neutralises_a_line_comment():
    result = validate_sql("SELECT label FROM film -- ; DELETE FROM film")
    assert "delete" not in result.lower()


def test_rejects_a_table_outside_the_allow_list():
    # query_log existe bien en base, mais elle contient les questions des autres
    # visiteurs : elle n'est pas dans l'allow-list.
    with pytest.raises(UnsafeQueryError):
        validate_sql("SELECT * FROM query_log")


def test_rejects_system_tables():
    with pytest.raises(UnsafeQueryError):
        validate_sql("SELECT * FROM pg_catalog.pg_tables")


def test_rejects_reading_files():
    with pytest.raises(UnsafeQueryError):
        validate_sql("SELECT pg_read_file('/etc/passwd')")


def test_rejects_an_empty_query():
    with pytest.raises(UnsafeQueryError):
        validate_sql("   ")


# ---------------------------------------------------------------
# plafonnement du nombre de rows
# ---------------------------------------------------------------


def test_adds_a_missing_limit():
    assert enforce_limit("SELECT label FROM film", 50) == "SELECT label FROM film LIMIT 50"


def test_keeps_a_reasonable_limit():
    sql = "SELECT label FROM film LIMIT 10"
    assert enforce_limit(sql, 50) == sql


def test_caps_a_limit_that_is_too_high():
    result = enforce_limit("SELECT label FROM film LIMIT 5000", 50)
    assert result.strip().endswith("LIMIT 50")
    assert "5000" not in result
