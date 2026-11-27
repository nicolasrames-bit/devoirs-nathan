# 🎒 Portail école de Nathan — Webtop → portail famille + Google Calendar

Plusieurs fois par jour (vers 14h, 17h et 20h, du dimanche au vendredi), GitHub :
1. se connecte à Webtop (Smart School) et récupère les devoirs ;
2. publie un **portail web privé** (mot de passe famille, données chiffrées) avec 3 onglets :
   - **Aujourd'hui** : les cours du jour, ceux du prochain jour d'école + ce qu'il faut mettre dans le cartable, les devoirs à faire ;
   - **Emploi du temps** : jour par jour, cette semaine et la suivante (cours annulés et remplacements signalés) ;
   - **Devoirs** : tous les devoirs, filtrables par matière, avec une case ✓ « fait » ;
3. ajoute les devoirs dans un agenda **Google Calendar** « Devoirs Nathan ».

Rien ne tourne sur ton PC. Aucun identifiant n'est dans le code : tout est dans les *Secrets* GitHub.

---

## 1. Créer le repo (5 min)

1. Repo `devoirs-nathan` sur GitHub.
   *GitHub Pages sur un repo **privé** nécessite GitHub Pro. Sans Pro, passe le repo en **Public**
   (Settings → General → Danger Zone → Change visibility) : il ne contient que le code, les données
   sont publiées chiffrées et les logs n'affichent aucune donnée.*
2. Les fichiers sont déjà dans le repo.
3. **Settings → Pages → Build and deployment → Source : GitHub Actions**.

## 2. Ajouter les secrets

**Settings → Secrets and variables → Actions → New repository secret** :

| Nom | Valeur |
|---|---|
| `WEBTOP_USERNAME` | identifiant Webtop |
| `WEBTOP_PASSWORD` | mot de passe Webtop |
| `FAMILY_PASSWORD` | un mot de passe que tu choisis pour la page famille (à donner à Tania et Nathan) |

## 2 bis. Relais Israël (obligatoire)

Webtop bloque les connexions venant de l'étranger, et GitHub tourne aux États-Unis.
Un mini-relais dans le data center Google de **Tel Aviv** fait passer uniquement les appels Webtop
(protégé par un jeton secret, il ne stocke rien). Coût : gratuit ou quelques centimes par mois.

1. <https://console.cloud.google.com> → crée (ou choisis) un projet, par ex. `devoirs-nathan`.
   La facturation doit être activée sur le projet (menu *Facturation*), Cloud Functions l'exige.
2. Clique sur l'icône **Cloud Shell** `>_` en haut à droite, puis colle :
   ```
   bash <(curl -fsSL https://raw.githubusercontent.com/nicolasrames-bit/devoirs-nathan/main/relay/setup-cloudshell.sh)
   ```
3. À la fin, le script affiche 2 valeurs : ajoute-les comme secrets GitHub
   `WEBTOP_RELAY_URL` et `WEBTOP_RELAY_TOKEN`.

Le même projet Google Cloud servira pour Google Calendar (étape 4).

## 3. Premier lancement

**Actions → Sync devoirs → Run workflow**, coche **Mode diagnostic** → Run.

- ✅ Vert : ouvre `https://<ton-user>.github.io/devoirs-nathan/`, entre le mot de passe famille.
  Sur iPhone/Android : « Ajouter à l'écran d'accueil » pour en faire une icône.
- ❌ Rouge : voir « Dépannage » plus bas.

> Vérifie une chose au premier lancement : la date affichée pour chaque devoir correspond-elle au jour
> **où il faut le rendre** ou au jour **où il a été donné** ? (Webtop ne le documente pas.)

## 4. Google Calendar (15 min, une seule fois)

1. Va sur <https://console.cloud.google.com> → crée un projet `devoirs-nathan`.
2. **APIs & Services → Library** → active **Google Calendar API**.
3. **IAM & Admin → Service accounts → Create service account** (nom : `devoirs`) → Done.
   Clique dessus → **Keys → Add key → Create new key → JSON** → un fichier se télécharge.
   Note l'e-mail du compte de service (`devoirs@…iam.gserviceaccount.com`).
4. Dans **Google Calendar** : *Autres agendas → + → Créer un agenda* → « Devoirs Nathan ».
5. Paramètres de cet agenda :
   - **Partager avec des personnes** → ajoute l'e-mail du compte de service → *Apporter des modifications aux événements*.
   - Ajoute aussi Tania si elle veut le voir.
   - **Notifications pour les événements d'une journée entière** → *1 jour avant à 19:00*
     (c'est ce réglage qui envoie le rappel la veille).
   - **Intégrer l'agenda → ID de l'agenda** : copie-le.
6. Ajoute 2 secrets GitHub :
   - `GOOGLE_SERVICE_ACCOUNT_JSON` → colle **tout le contenu** du fichier JSON
   - `GOOGLE_CALENDAR_ID` → l'ID copié
7. Supprime le fichier JSON de ton ordinateur.

Au prochain lancement, les devoirs apparaissent comme événements « 📚 Matière (Nathan) » sur la journée.
Si un devoir à venir disparaît de Webtop, il est aussi retiré de l'agenda.

---

## Dépannage

| Message dans le log | Solution |
|---|---|
| `Connexion Webtop échouée` | Vérifie les identifiants. Si c'est bon, l'école utilise une autre clé : ouvre webtop.co.il dans Chrome, F12 → Network, connecte-toi, ouvre la requête `LoginByUserNameAndPassword` et copie la valeur `Data` dans un secret `WEBTOP_DATA`. |
| `classCode: MANQUANT` | Relance en mode diagnostic, et envoie à Claude la partie `LOGIN data` / `DASHBOARD` du log (seulement des noms de champs, aucune donnée). En attendant, tu peux renseigner `WEBTOP_CLASS_CODE` et `WEBTOP_CLASS_NUMBER` (niveau et numéro de classe, ex. 3 et 2 pour ג'2). |
| `Emploi du temps vide` ou cours aux mauvais jours | Envoie la partie `SCHEDULE` du log diagnostic à Claude. |
| `0 devoirs reçus` mais il y en a dans Webtop | Envoie la partie `HOMEWORK:` du log diagnostic à Claude pour ajuster la lecture des champs. |
| Plusieurs enfants sur le compte | Ajoute un secret `WEBTOP_STUDENT_NAME` = prénom de l'enfant. |

- Changer le mot de passe famille : modifie le secret `FAMILY_PASSWORD` puis relance (l'historique repart de zéro).
- La case ✓ « fait » est mémorisée sur chaque appareil séparément.
- Basé sur les endpoints de la librairie open-source [pywebtop](https://github.com/t0mer/pywebtop) — non officiel,
  peut casser si Smart School change son système.
