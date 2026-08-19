# Assistant IA pour interroger des données internes

**[Voir la démo](https://bkim-ai-data-assistant.vercel.app/fr)** · [Documentation d'architecture](ARCHITECTURE.md)

J'ai développé cette application web pour interroger en langage naturel une base
de données hébergée de manière privée dans le cloud. C'est une démonstration de
**comment brancher un LLM sur les données d'une entreprise**, pas un chatbot.

Le modèle transforme la demande en requête SQL, l'exécute sur la base avec un
compte **read-only**, puis rédige la réponse **uniquement à partir des rows
retournées**, jamais avec ses connaissances générales. L'interface affiche la
requête SQL générée, les données brutes et les sources, ce qui permet de le
vérifier directement.

Le dataset utilisé ici (11 696 films issus de Wikidata) n'est qu'un
démonstrateur. La même architecture s'applique à une base d'entreprise contenant
des clients, des commandes, des stocks, de la finance ou de la documentation
interne.

## Le pipeline

```
Question en langage naturel
        │
        ▼
1. LLM        → écrit une requête SQL, à partir du schema seul
        ▼
2. Validator  → un seul SELECT, tables allow-listed, LIMIT forcé
        ▼
3. Python interroge PostgreSQL avec un rôle read-only
        │  si la requête échoue, l'erreur de la base est renvoyée
        └─→ au LLM pour une seule tentative de correction
        ▼
4. LLM        → rédige la réponse à partir des rows retournées seules
        ▼
5. Affichage : réponse + SQL généré + rows + sources + temps de réponse
```

Si aucune row ne correspond, l'application le dit au lieu de deviner.

## Stack

| Couche | Technologie |
|---|---|
| Frontend | Next.js 16, TypeScript, Tailwind CSS |
| Backend | Python 3.12, FastAPI, SQLAlchemy, psycopg |
| Base de données | PostgreSQL 16 (Neon, serverless) |
| IA | API Anthropic, Claude Haiku 4.5 |
| Données | Wikidata, importées via son endpoint SPARQL |
| Qualité | pytest, ruff |
| Déploiement | Vercel (frontend) + Render (API) + Neon (base de données) |

## À propos de ce repository

Je conserve le code source complet dans un repository privé. Celui-ci présente la documentation d’architecture et un module représentatif, afin de montrer mes choix techniques et une partie de l’implémentation sans publier l’ensemble du code.

Je peux présenter le code complet sur demande.

### Le module publié ici

[`backend/app/sql_guard.py`](backend/app/sql_guard.py), avec ses tests dans
[`backend/tests/test_sql_guard.py`](backend/tests/test_sql_guard.py).

C’est la couche de sécurité. Je considère la génération de SQL par un LLM comme une surface d’attaque et j’ai mis en place plusieurs mécanismes pour la sécuriser:

1. **Je m'appuie sur un rôle PostgreSQL read-only** comme première protection. L'application se connecteavec un
    compte qui n'a aucun privilège d'écriture : une requête destructrice échoue donc au niveau de la base, même si les protections applicatives étaient contournées. J'ai également mis en place un script de vérification qui tente une écriture et vérifie que PostgreSQL la refuse.
2. **J'ai mis en place un validator** comme deuxième barrière, avant l'exécution. Il autorise un seul `SELECT`,
   utilise une allow-list de tables, bloque les schémas système, supprime les commentaires avant analyse et masque les chaînes de caractères pour qu'un film intitulé The `Update` par exemple ne déclenche pas le mot-clé interdit `update`.
3. **J'impose un `LIMIT` et un timeout** sur chaque requête.

Je mets l’accent sur deux des 24 tests : par exemple, un DROP caché dans un commentaire SQL
est neutralisé plutôt que simplement refusé, et un ; à l’intérieur d’une chaîne de caractères
est bien traité comme une donnée, et non comme un enchaînement d’instructions. 

J’ai volontairement limité le module à la bibliothèque standard, ce qui permet d’exécuter
les tests de manière autonome :  

```bash
cd backend && pip install pytest && pytest
```

J’ai volontairement placé ces protections dans cet ordre : je pense que le privilège constitue
la garantie principale, tandis que le validator apporte une défense en profondeur.
Un filtre peut par exemple être contourné, alors qu’un privilège qui n’existe pas ne peut pas l’être.  


## Auteur

Benie Kimpuela, Bachelor Développeuse Web & IA, Efrei
kpbenie@gmail.com

---

© 2026 Benie Kimpuela. Tous droits réservés. Aucune licence de réutilisation
n'est accordée pour ce code.
