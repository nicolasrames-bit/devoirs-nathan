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

## 1 bis. Comment le script se connecte (important)

La connexion Webtop par identifiant est protégée par un contrôle anti-robot (Cloudflare) :
le script **ne se connecte jamais lui-même**. Il réutilise la session qu'un parent ouvre dans son navigateur.

**Renouveler la session** (quand le portail affiche « session expirée ») :
1. Chrome → webtop.smartschool.co.il → connexion avec l'identifiant parent (coche « se souvenir de moi » si proposé).
2. F12 → Network → Fetch/XHR → F5 → clique sur `GetMenuCounters` → Headers → Request Headers → ligne `Cookie`.
3. Copie la partie `webToken=…` dans le secret GitHub **`WEBTOP_SESSION`** (ne la partage nulle part ailleurs).
4. Ne clique pas sur « התנתק » ensuite (ça fermerait la session) : ferme simplement l'onglet.

**Ce que Webtop fournit pour ce compte** : les cours du jour (matière, heure, prof, sujet, annulations) et les devoirs
donnés ce jour-là. L'emploi du temps de la semaine n'est pas autorisé par l'école : l'onglet *Emploi du temps* se
remplit au fil des jours.

## 2. Ajouter les secrets

**Settings → Secrets and variables → Actions → New repository secret** :

| Nom | Valeur |
|---|---|
| `WEBTOP_SESSION` | la session Webtop du parent (voir 1 bis) |
| `FAMILY_PASSWORD` | un mot de passe que tu choisis pour la page famille (à donner à Tania et Nathan) |

## 2 bis. Ordinateur de la maison (obligatoire)

Webtop n'accepte que les connexions « maison » en Israël : il refuse les serveurs de GitHub et même
les serveurs cloud israéliens. L'étape Webtop tourne donc sur un ordinateur de la maison
(« self-hosted runner »). Si l'ordinateur est éteint, la synchro attend qu'il se rallume (jusqu'à 24 h).
Le portail, lui, reste en ligne en permanence.

**a. Installer Python** (une seule fois) — <https://www.python.org/downloads/>
- Coche **Add python.exe to PATH**, puis **Customize installation** → Next → coche **Install Python for all users** → Install.

**b. Installer le runner GitHub**
1. Dans le repo : **Settings → Actions → Runners → New self-hosted runner** → choisis **Windows**, **x64**.
2. Ouvre **PowerShell en administrateur** (clic droit sur Démarrer → *Terminal (administrateur)*).
3. Copie-colle les commandes affichées par GitHub, bloc par bloc (*Download* puis *Configure*).
   - Crée d'abord le dossier ailleurs que dans `C:\Users\...`, par ex. `mkdir C:\actions-runner; cd C:\actions-runner`.
   - Pendant `config.cmd`, appuie sur **Entrée** pour toutes les questions, sauf :
     *Would you like to run the runner as service?* → **Y** (il démarre tout seul avec Windows).
4. Dans **Settings → Actions → Runners**, le runner doit apparaître **Idle** (vert).

**c. Sécurité (repo public)** — **Settings → Actions → General → Approval for running fork pull request workflows
from contributors** → choisis **Require approval for all external contributors** → Save.
Ainsi, personne d'autre ne peut faire exécuter quoi que ce soit sur ton ordinateur.

> Le dossier `relay/` (relais Google Cloud) n'est plus utilisé : Webtop bloque aussi les serveurs cloud.

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

## 4 bis. Emploi du temps habituel

Secret **`WEEKLY_TIMETABLE`** : une ligne par cours — `jour heure matière | prof | début-fin`, jours א à ו.
Exemple : `א 1 תורה | רוני ויצמן | 8:00-8:50`. Les lignes qui commencent par `#` sont ignorées.
Le portail affiche cet horaire pour toute la semaine ; quand Webtop donne les vrais cours du jour
(annulations, sujet, devoirs), ils remplacent l'horaire habituel pour ce jour-là.
À mettre à jour à chaque rentrée.

## 5. Exercices adaptés (optionnel)

À chaque synchro, pour les cours des 3 derniers jours, Claude génère 4 petites questions en hébreu sur le sujet
du cours (onglet **🧠 Exercices** du portail). Seuls la matière, le sujet du cours et le texte des devoirs sont
envoyés — jamais le nom de l'enfant. Chaque cours n'est traité qu'une fois.

1. <https://console.anthropic.com> → crée un compte → **Billing** : ajoute un petit crédit (quelques dollars suffisent pour longtemps).
2. **API Keys → Create Key** → copie la clé.
3. Secret GitHub **`ANTHROPIC_API_KEY`** = cette clé.
4. Le niveau est détecté automatiquement ; pour le forcer : **Settings → Secrets and variables → Actions → onglet Variables**
   → `STUDENT_GRADE` = `3` (par exemple).

## 6. Pièces et Robux

- Bonne réponse : +10 🪙 (1er essai d'une série seulement) · série terminée +10 · série parfaite +10
- Devoir coché « fait » (de moins de 7 jours) : +20 🪙
- Bonus d'examen ajoutés par les parents : +300 / +500
- **800 🪙 = 400 Robux (4,99 $)**, au plus **2 packs par semaine** (9,98 $)
- **Espace parents** (en bas de l'onglet Exercices, code choisi à la 1re ouverture) : bilan de la semaine,
  bonus d'examen, bouton « J'ai donné 400 Robux ».
- Les pièces sont enregistrées sur l'appareil de l'enfant : utiliser toujours le même.

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
