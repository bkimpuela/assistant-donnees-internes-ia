# Architecture

> **En bref.** J'ai développé cette application web pour interroger en langage
> naturel une base de données hébergée de manière privée dans le cloud. Le modèle
> transforme la demande en requête SQL, interroge la base, et rédige la réponse
> uniquement à partir des données qu'il a reçues en retour.
>
> Mon objectif est de montrer comment on peut brancher un LLM sur des données
> internes tout en gardant le contrôle sur ce qu'il peut atteindre. Le cinéma
> n'est ici qu'un démonstrateur : la même architecture s'applique à des données
> d'entreprise, clients, commandes, stocks, finance ou documentation interne.

---

## Ce que je démontre ici

**L’objectif du projet est de permettre à des employés d’interroger les données de l’entreprise en langage naturel, tout en évitant que le modèle réponde à partir de ses connaissances générales, accède à des données qu’il ne devrait pas voir ou expose publiquement les données auxquelles il a accès. La question est donc : comment y parvenir ?**

Cette question a trois volets, et j’ai traité chacun d’eux :

| Le problème | Ma réponse dans ce projet |
|---|---|
| Le modèle ne doit rien inventer | Il ne voit jamais la base. Il voit un schema, écrit du SQL, et ne reçoit ensuite que les rows retournées par cette requête. |
| Le modèle ne doit pas atteindre de données non autorisées | Le compte de base de données qu'il utilise ne peut physiquement pas écrire, et une allow-list limite les tables qu'une requête générée peut toucher. |
| Un Visiteur doit pouvoir le vérifier | L'interface affiche la requête SQL générée, les rows brutes et les liens vers les sources, pour chaque réponse. |

## La pipeline

```
Question de l'utilisateur (langage naturel)
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │ 1. Question Utilisateur                     │
  │    le LLM analyse la question + le schema   │   generation
  │    Puis génère la requête SQL               │     
  └─────────────────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │ 2. Validator                                │   control
  │    Contrôle la requête avant exécution :    │
  │    SELECT uniquement, tables autorisées,    │
  │    accès système bloqué, n° de lignes limité│
  └─────────────────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │ 3. Python exécute la requête                │   Retrieval
  │    PostgreSQL avec un rôle read-only        │
  │    Transaction read-only + timeout 5 s      │
  └─────────────────────────────────────────────┘
        │  rows
        ▼
  ┌─────────────────────────────────────────────┐
  │ 4. Claude génère la réponse                 │
  │    Question utilisateur + lignes retournées │     Augmentation
  │    réponse basée uniquement sur ces données │     + generation
  └─────────────────────────────────────────────┘
        │
        ▼
Réponse + SQL généré + lignes retournées + sources + temps de réponse
```

Si l'étape 3 échoue, je renvoie l'erreur de la base au modèle pour **une** seule
tentative de correction, puis j'abandonne. La réponse contient un flag
`repaired`, ce qui permet à l'interface de montrer que le système s'est corrigé
tout seul.

## Pourquoi du text-to-SQL plutôt qu'une vector search

Quand on parle de RAG, on pense en général à des documents transformés en
embeddings, puis retrouvés par similarité. C'est le bon outil pour du texte non
structuré : contrats, wiki interne, tickets de support.

Ici, ce n'est pas le bon outil. Les questions portent sur des données **structurées** : *les films
sortis entre 2015 et 2020*, *les acteurs qui ont joué dans plusieurs films de Tim
Burton*. Une recherche par similarité ne sait pas compter, ni filtrer sur un
intervalle, ni faire une jointure. Une base relationnelle le permet, et un LLM est utilisé pour générer les requêtes SQL.

La logique retrieval → augmentation → generation reste la même. Seule l’étape de retrieval change : ici, j’utilise une requête SQL plutôt qu’une vector search.

## Contrôler ce que le modèle peut atteindre

Quatre couches indépendantes, qui protègent chacune un niveau différent :

1. **Rôle read-only sur la base.** L'application se connecte avec le compte
   `app_readonly`, qui n'a aucun privilège `INSERT`, `UPDATE` ou `DELETE`. C'est
   la protection principale : même si les autres couches sont contournées, la
   base refuse toute écriture.
   `sql/roles.sql`
2. **Validator des requêtes.** Avant l'exécution, je vérifie qu'il n'y a qu'une
   seule instruction, obligatoirement un `SELECT`, que les tables sont autorisées
   et que les schémas système de PostgreSQL sont bloqués. Les commentaires sont
   supprimés et les string literals masqués avant l'analyse, pour qu'un film
   intitulé *The Update* ne déclenche pas le mot-clé interdit `update`. 24 tests.
   [`app/sql_guard.py`](backend/app/sql_guard.py)
3. **Limites d'exécution.** J'utilise une transaction read-only, un
   `statement_timeout` de 5 secondes et j'impose un `LIMIT` à chaque requête.
   `app/db.py`
4. **Rate limiting.** Je limite les questions à dix par minute et par adresse IP.
   La démo est publique et chaque question entraîne deux appels API : cette
   limite protège donc à la fois le service et les coûts.

J'ai volontairement placé ces protections dans cet ordre : je pense que le privilège constitue
la garantie principale, tandis que le validator apporte une défense en profondeur.
Un filtre peut par exemple être contourné, alors qu’un privilège
qui n’existe pas ne peut pas l’être.  

## Le dataset

11 696 films sortis entre 1930 et 2024, importés depuis l'endpoint SPARQL de
Wikidata : titres, durées, box office, genres, pays, réalisateurs et casting.
Cela représente 48 844 personnes et 129 481 liens de casting, soit environ 37 Mo
dans PostgreSQL.

L'import (`scripts/import_wikidata.py`) est idempotent : le relancer met à jour
les lignes existantes au lieu de les dupliquer, donc un import interrompu peut
simplement être repris.

## Stack

| Couche | Mon choix | Pourquoi |
|---|---|---|
| Interface | Next.js 16, TypeScript, Tailwind | React rendu côté serveur ; l'interface doit montrer le pipeline, pas seulement la réponse |
| API | Python 3.12, FastAPI | Modèles de request et de response typés, et documentation d'API générée automatiquement |
| Base de données | PostgreSQL 16 sur Neon | Serverless, et le role read-only est appliqué par la base elle-même |
| Modèle | Claude Haiku 4.5 (Anthropic) | Rapide et peu cher ; la tâche est de la traduction et de la reformulation, pas du raisonnement |
| Packaging | Docker | L'API tourne à l'identique sur n'importe quel host, en non-root |
| Hosting | Vercel (interface) + Render (API) | Free tiers, déployés directement depuis le repository |

## Notes techniques

Les parties qui m'ont vraiment demandé du travail, et ce qu'elles m'ont appris :

**Wikidata enregistre les ressorties comme des dates de sortie supplémentaires.**
*The Dark Knight* était daté de 2022 à cause d'une ressortie en IMAX. Ma première
correction, garder la date la plus ancienne au moment de la mise à jour, n'a pas
marché : chaque requête annuelle est plafonnée, donc l'enregistrement de 2008 du
film n'était jamais récupéré et il n'y avait aucune date antérieure à comparer.
La correction qui marche demande directement à Wikidata la date de sortie la plus
ancienne de chaque film. Sur un échantillon, 6 films sur 26 étaient mal datés.

**Quand un modèle écrit du mauvais SQL, il faut d'abord soupçonner le prompt.**
Mes requêtes échouaient sans arrêt sur `person.label_en`, une colonne qui
n'existe pas. La description du schema que je donnais au modèle affirmait que
toutes les tables l'avaient. Le modèle suivait fidèlement mes instructions ;
c'étaient mes instructions qui étaient fausses.

**Il faut dire au modèle ce que la donnée ne contient pas.** Les notes IMDb ne
sont présentes que pour 4 % des films. Des questions comme « un bon thriller »
ne renvoyaient rien, jusqu'à ce que le prompt indique la couverture réelle de
chaque colonne et interdise au modèle de filtrer sur les colonnes creuses sans
demande explicite.

**Les écritures en masse doivent survivre au réseau.** Mon import a perdu une
heure de travail quand la base a fermé la connexion au milieu d'un batch.
J'écris maintenant par lots, avec une nouvelle tentative après réinitialisation
du connection pool.

## Les langues

L'interface est servie en français et en anglais, sur deux routes séparées :
`/fr` et `/en`. Les deux sont pré-générées en pages statiques au build, donc
chacune a son URL partageable. Un visiteur qui arrive sur `/` est redirigé selon
le header `Accept-Language` de son navigateur, avec le français par défaut.

La traduction ne s'arrête pas à l'interface. La langue est envoyée avec chaque
question, et l'API l'utilise pour ses propres messages, y compris les raisons que
donne le validator quand il refuse une requête. Le texte est centralisé à un seul
endroit de chaque côté : `app/i18n.py` côté backend, `dictionaries/` côté
frontend. Les modules qui détectent un problème lèvent un code court, jamais une
phrase.

La réponse elle-même est rédigée dans la langue de l'interface, pas dans celle de
la question. Au départ je demandais au modèle de la deviner, et il se trompait
souvent : le payload qu'il reçoit est plein de clés en français et de titres de
films en français, ce qui le tirait vers le français même pour une question en
anglais. L'interface sait déjà ce que le visiteur lit, donc autant le lui dire
explicitement.

## Limites

Je les énonce clairement, parce que ce sont les vraies limites de la
démonstration :

- **Pas de fallback vers les données en temps réel.** Si un film est absent du
  sous-ensemble importé, la réponse est « non trouvé » plutôt qu'une recherche
  live sur Wikidata. Un système en production dans une entreprise n'en aurait pas
  besoin, puisque la base interne fait autorité.
- **Pas d'authentification.** La démo est publique par choix. Un vrai déploiement
  ajouterait un accès par utilisateur, et pourrait le descendre dans la base avec
  de la row-level security, pour que l'autorisation soit appliquée là où vivent
  les données.
- **Un seul domaine.** Le schema est taillé pour le cinéma. Le pointer vers une
  autre base demande de réécrire la description du schema donnée au modèle et
  l'allow-list des tables ; le pipeline, lui, ne change pas.
- **Le free tier se met en veille.** La première requête après une période
  d'inactivité prend environ 50 secondes, le temps que le container de l'API se
  réveille.

## Appliquer ça à une vraie entreprise

Deux choses seulement dépendent du domaine : la **description du schema** donnée
au modèle (`app/llm.py`) et l'**allow-list des tables**
([`app/sql_guard.py`](backend/app/sql_guard.py)). On les pointe vers une autre
base, clients, commandes, stocks, documentation interne, et tout le reste du
pipeline fonctionne sans changement.

Pour un déploiement en production, les ajouts qui valent le coup sont :
l'authentification avec un compte de base de données par role, de la row-level
security pour que l'autorisation soit appliquée par PostgreSQL plutôt que par
l'application, et un audit log de chaque requête générée. La table de logs est
déjà dans le schema pour cette raison.
