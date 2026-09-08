import pandas as pd
from datetime import datetime, time
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from .forms import MultipleImportForm
from Collaborateur.models import Collaborateur, Departement, Unite
from declaration_effectif.models import historique
from .models import histo_import, histo_import_detail
from utilisateur.decorators import role_required
from declaration_effectif.models import declaration_effectif as DeclarationEffectif
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from django.utils import timezone
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

# ------------------------------------------------------------------
# FONCTIONS AUXILIAIRES
# ------------------------------------------------------------------

def clean_val(val):
    if val is None or pd.isna(val):
        return None
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()

def clean_int(val, default=0):
    try:
        if pd.isna(val) or val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default

def read_uploaded_file(fichier):
    """
    OPTIM :
    - dtype=str évite l'inférence de type colonne par colonne (coûteuse) et
      les surprises du style matricule "1234.0".
    - engine="calamine" (pip install python-calamine) est un moteur Rust
      nettement plus rapide qu'openpyxl sur les gros fichiers Excel.
      Fallback automatique sur openpyxl si le paquet n'est pas installé.
    """
    if fichier.name.endswith(".csv"):
        df = pd.read_csv(fichier, dtype=str)
    else:
        try:
            df = pd.read_excel(fichier, engine="calamine", dtype=str)
        except ImportError:
            df = pd.read_excel(fichier, dtype=str)
    df.columns = df.columns.str.strip()
    return df

def iter_rows(df):
    """
    OPTIM : to_dict("records") est nettement plus rapide que df.iterrows(),
    qui reconstruit une Series pandas (avec tout son overhead) à chaque tour
    de boucle. On garde l'index (i) pour les messages d'erreur "Ligne {i+2}".
    """
    return enumerate(df.to_dict("records"))

# ------------------------------------------------------------------
# IMPORT DÉPARTEMENTS
# ------------------------------------------------------------------

def importer_departements(df_dpt, erreurs, collaborateurs_map=None):
    # OPTIM : collaborateurs_map peut être fourni par l'appelant pour éviter
    # de refaire la même requête que importer_collaborateurs().
    if collaborateurs_map is None:
        collaborateurs_map = {c.it: c for c in Collaborateur.objects.all()}

    departements_existants = {d.abreviation: d for d in Departement.objects.all()}

    a_creer = []
    a_maj = []

    for i, row in iter_rows(df_dpt):
        try:
            abbrev = clean_val(row.get("Abreviation") or row.get("abreviation"))
            if not abbrev:
                erreurs.append(f"Départements Ligne {i+2}: Abréviation manquante.")
                continue

            nom_dpt = row.get("nom_departement") or abbrev
            rh_val = clean_val(row.get("HRBP"))
            admin_val = clean_val(row.get("ADMIN"))
            drh_val = clean_val(row.get("DRH"))

            rh_obj = collaborateurs_map.get(rh_val) if rh_val else None
            admin_obj = collaborateurs_map.get(admin_val) if admin_val else None
            drh_obj = collaborateurs_map.get(drh_val) if drh_val else None

            if rh_val and not rh_obj:
                erreurs.append(f"Départements Ligne {i+2}: HRBP '{rh_val}' introuvable.")
            if admin_val and not admin_obj:
                erreurs.append(f"Départements Ligne {i+2}: ADMIN '{admin_val}' introuvable.")
            if drh_val and not drh_obj:
                erreurs.append(f"Départements Ligne {i+2}: DRH '{drh_val}' introuvable.")

            data = dict(
                nom_departement=nom_dpt,
                HRBP=rh_obj,
                ADMIN=admin_obj,
                DRH=drh_obj,
                maquette=clean_int(row.get("Maquette")),
            )

            if abbrev in departements_existants:
                obj = departements_existants[abbrev]
                for k, v in data.items():
                    setattr(obj, k, v)
                a_maj.append(obj)
            else:
                obj = Departement(abreviation=abbrev, **data)
                a_creer.append(obj)
                departements_existants[abbrev] = obj

        except Exception as e:
            erreurs.append(f"Départements Ligne {i+2}: {e}")

    with transaction.atomic():
        if a_creer:
            Departement.objects.bulk_create(a_creer, batch_size=1000)
        if a_maj:
            Departement.objects.bulk_update(
                a_maj, ["nom_departement", "HRBP", "ADMIN", "DRH", "maquette"], batch_size=1000
            )

    return len(a_creer) + len(a_maj)

# ------------------------------------------------------------------
# IMPORT UNITÉS
# ------------------------------------------------------------------

def importer_unites(df_unite, erreurs):
    unites_existantes = {u.abreviation: u for u in Unite.objects.all()}
    a_creer = []
    a_maj = []

    for i, row in iter_rows(df_unite):
        try:
            abbrev = clean_val(row.get("abreviation") or row.get("Abreviation"))
            if not abbrev:
                erreurs.append(f"Unités Ligne {i+2}: Abréviation manquante.")
                continue

            data = dict(
                nom=row.get("nom") or abbrev,
                maquette=clean_int(row.get("maquette"), 0),
                A=clean_int(row.get("A"), 0),
                T=clean_int(row.get("T"), 0),
                P=clean_int(row.get("P"), 0),
                C=clean_int(row.get("C"), 0),
            )

            if abbrev in unites_existantes:
                obj = unites_existantes[abbrev]
                for k, v in data.items():
                    setattr(obj, k, v)
                a_maj.append(obj)
            else:
                obj = Unite(abreviation=abbrev, **data)
                a_creer.append(obj)
                unites_existantes[abbrev] = obj

        except Exception as e:
            erreurs.append(f"Unités Ligne {i+2}: {e}")

    with transaction.atomic():
        if a_creer:
            Unite.objects.bulk_create(a_creer, batch_size=1000)
        if a_maj:
            Unite.objects.bulk_update(a_maj, ["nom", "maquette", "A", "T", "P", "C"], batch_size=1000)

    return len(a_creer) + len(a_maj)

# ------------------------------------------------------------------
# IMPORT COLLABORATEURS — avec suivi détaillé
# ------------------------------------------------------------------

# Champs à comparer pour détecter une "vraie" modification
CHAMPS_SUIVIS = ["matricule", "nom_complete", "lot", "departement", "unite", "eq", "shift", "sexe"]

def _val_affichable(v):
    """Convertit une valeur (y compris FK) en texte lisible pour le détail d'import."""
    if v is None:
        return ""
    if hasattr(v, "abreviation"):
        return v.abreviation
    return str(v)

def importer_collaborateurs(df_collab, erreurs, import_log=None, collaborateurs_map=None):
    """
    Retourne un tuple :
        (nb_lignes_traitees, collaborateurs_dans_fichier)
    où collaborateurs_dans_fichier est le set des "it" présents dans le
    fichier importé. Ce set est réutilisé ensuite pour comparer l'état du
    fichier aux dernières déclarations enregistrées dans declaration_effectif.
    """
    departements_map = {d.abreviation: d for d in Departement.objects.all()}
    unites_map = {u.abreviation: u for u in Unite.objects.all()}

    # OPTIM : collaborateurs_map peut être fourni par l'appelant (partagé avec
    # importer_departements) pour éviter de charger deux fois toute la table.
    if collaborateurs_map is None:
        collaborateurs_map = {c.it: c for c in Collaborateur.objects.all()}

    collaborateurs_par_matricule = {
        c.matricule: c.it for c in collaborateurs_map.values() if c.matricule
    }

    a_creer = []
    a_maj = []
    ru_a_resoudre = []
    collaborateurs_dans_fichier = set()
    details_a_creer = []  # histo_import_detail en attente

    for i, row in iter_rows(df_collab):
        try:
            utilisateur_it = clean_val(row.get("Utilisateur"))
            if not utilisateur_it:
                erreurs.append(f"Collaborateurs Ligne {i+2}: Identifiant Utilisateur manquant.")
                continue

            collaborateurs_dans_fichier.add(utilisateur_it)

            dpt_code = clean_val(row.get("DPT"))
            dpt_obj = departements_map.get(dpt_code) if dpt_code else None
            if dpt_code and not dpt_obj:
                erreurs.append(f"Collaborateurs Ligne {i+2}: Département '{dpt_code}' introuvable.")

            unite_code = clean_val(row.get("Unite"))
            unite_obj = unites_map.get(unite_code) if unite_code else None
            if unite_code and not unite_obj:
                erreurs.append(f"Collaborateurs Ligne {i+2}: Unité '{unite_code}' introuvable.")

            ru_mat = clean_val(row.get("RU"))
            nom = str(row.get("Nom") or "").strip()
            prenom = str(row.get("Prénom") or "").strip()

            data = dict(
                matricule=clean_val(row.get("Matricule")),
                nom_complete=f"{nom} {prenom}".strip(),
                lot=str(row.get("Lot", "") or "").strip(),
                departement=dpt_obj,
                unite=unite_obj,
                eq=str(row.get("Equipe", "") or ""),
                shift=clean_val(row.get("Shift")),
                sexe=clean_int(row.get("Sexe"), default=1),
            )

            matricule_val = data["matricule"]

            # OPTIM : on construit systématiquement un objet Collaborateur "prêt
            # à insérer", qu'il soit nouveau ou existant. Il est ensuite envoyé
            # en une seule passe via bulk_create(update_conflicts=True), ce qui
            # évite la double opération bulk_create + bulk_update d'origine.
            obj = Collaborateur(it=utilisateur_it, **data)

            if utilisateur_it in collaborateurs_map:
                ancien = collaborateurs_map[utilisateur_it]

                if import_log is not None:
                    for champ in CHAMPS_SUIVIS:
                        ancienne = getattr(ancien, champ)
                        nouvelle = data[champ]
                        if ancienne != nouvelle:
                            details_a_creer.append(histo_import_detail(
                                import_parent=import_log,
                                action="MODIFICATION",
                                matricule=matricule_val,
                                it=utilisateur_it,
                                nom_complete=data["nom_complete"],
                                champ_modifie=champ,
                                ancienne_valeur=_val_affichable(ancienne),
                                nouvelle_valeur=_val_affichable(nouvelle),
                            ))

                a_maj.append(obj)
            else:
                a_creer.append(obj)

                if import_log is not None:
                    details_a_creer.append(histo_import_detail(
                        import_parent=import_log,
                        action="CREATION",
                        matricule=matricule_val,
                        it=utilisateur_it,
                        nom_complete=data["nom_complete"],
                    ))

            collaborateurs_map[utilisateur_it] = obj

            if matricule_val:
                collaborateurs_par_matricule[matricule_val] = utilisateur_it

            if ru_mat:
                ru_a_resoudre.append((utilisateur_it, ru_mat))

        except Exception as e:
            erreurs.append(f"Collaborateurs Ligne {i+2}: {e}")
            if import_log is not None:
                details_a_creer.append(histo_import_detail(
                    import_parent=import_log,
                    action="ERREUR",
                    message_erreur=str(e),
                ))

    # Collaborateurs présents en base mais absents du nouveau fichier
    collaborateurs_a_supprimer = set(collaborateurs_map.keys()) - collaborateurs_dans_fichier

    if import_log is not None:
        for it_supp in collaborateurs_a_supprimer:
            c = collaborateurs_map[it_supp]
            details_a_creer.append(histo_import_detail(
                import_parent=import_log,
                action="SUPPRESSION",
                matricule=getattr(c, "matricule", None),
                it=it_supp,
                nom_complete=getattr(c, "nom_complete", None),
            ))

    with transaction.atomic():
        tous_objets = a_creer + a_maj
        if tous_objets:
            # OPTIM : une seule requête bulk pour créer ET mettre à jour,
            # au lieu de bulk_create() + bulk_update() séparés.
            Collaborateur.objects.bulk_create(
                tous_objets,
                update_conflicts=True,
                unique_fields=["it"],
                update_fields=[
                    "matricule", "nom_complete", "lot",
                    "departement", "unite", "eq", "shift", "sexe",
                ],
                batch_size=1000,
            )
        if collaborateurs_a_supprimer:
            Collaborateur.objects.filter(it__in=collaborateurs_a_supprimer).delete()
        if details_a_creer:
            histo_import_detail.objects.bulk_create(details_a_creer, batch_size=1000)

    if ru_a_resoudre:
        tous_les_collabs = {
            c.it: c for c in Collaborateur.objects.filter(it__in=[u for u, _ in ru_a_resoudre])
        }

        a_maj_ru = []
        for utilisateur_it, ru_val in ru_a_resoudre:
            collab = tous_les_collabs.get(utilisateur_it)
            if collab is None:
                continue

            ru_it_resolu = ru_val if ru_val in collaborateurs_map else None
            if not ru_it_resolu:
                ru_it_resolu = collaborateurs_par_matricule.get(ru_val)

            if ru_it_resolu:
                collab.ru_it_id = ru_it_resolu
                a_maj_ru.append(collab)
            else:
                erreurs.append(
                    f"RU '{ru_val}' introuvable (ni comme 'it', ni comme matricule) pour le collaborateur '{utilisateur_it}'."
                )

        if a_maj_ru:
            Collaborateur.objects.bulk_update(a_maj_ru, ["ru_it"], batch_size=1000)

    # Mise à jour des compteurs sur le log si fourni
    if import_log is not None:
        import_log.depar = len(a_creer)
        import_log.modif = len([d for d in details_a_creer if d.action == "MODIFICATION"])
        import_log.supprime = len(collaborateurs_a_supprimer)
        import_log.erreur = len(erreurs)
        import_log.save(update_fields=["depar", "modif", "supprime", "erreur"])

    return len(a_creer) + len(a_maj) + len(collaborateurs_a_supprimer), collaborateurs_dans_fichier

# ------------------------------------------------------------------
# IMPORT CHANGEMENTS D'AFFECTATION
# ------------------------------------------------------------------

def importer_changements(df_chg, erreurs):
    departements_map = {d.abreviation: d for d in Departement.objects.all()}
    a_creer = []

    for i, row in iter_rows(df_chg):
        try:
            collaborateur = clean_val(row.get("Nom & prénom"))
            if not collaborateur:
                erreurs.append(f"Changements Ligne {i+2}: Nom du collaborateur manquant.")
                continue

            initial = clean_val(row.get("Ru (Initial)")) or ""
            acceuil = clean_val(row.get("RU D'accueil")) or ""
            etat = clean_val(row.get("Etat")) or ""

            dpt_init_code = clean_val(row.get("DPT"))
            dpt_acceuil_code = clean_val(row.get("DPT (accueil)"))

            dpt_init_obj = departements_map.get(dpt_init_code) if dpt_init_code else None
            dpt_acceuil_obj = departements_map.get(dpt_acceuil_code) if dpt_acceuil_code else None

            if dpt_init_code and not dpt_init_obj:
                erreurs.append(f"Changements Ligne {i+2}: Département initial '{dpt_init_code}' introuvable.")
            if dpt_acceuil_code and not dpt_acceuil_obj:
                erreurs.append(f"Changements Ligne {i+2}: Département d'accueil '{dpt_acceuil_code}' introuvable.")

            obj = historique(
                collaborateur=collaborateur[:30],
                initial=initial[:30],
                acceuil=acceuil[:30],
                etat=etat[:30],
                dpt_init=dpt_init_obj,
                dpt_acceuil=dpt_acceuil_obj,
            )
            a_creer.append(obj)
        except Exception as e:
            erreurs.append(f"Changements Ligne {i+2}: {e}")

    with transaction.atomic():
        if a_creer:
            historique.objects.bulk_create(a_creer, batch_size=1000)

    return len(a_creer)

# ------------------------------------------------------------------
# SUIVI DES DÉCLARATIONS (declaration_effectif) VS FICHIER IMPORTÉ
# ------------------------------------------------------------------

def _snapshot_ru(collaborateurs_map):
    """
    Capture l'affectation (ru_it_id) de chaque collaborateur AVANT le
    traitement du nouveau fichier. Sert de référence pour "l'ancienne
    affectation" lors de la comparaison avec les déclarations de changement.
    """
    return {it: getattr(c, "ru_it_id", None) for it, c in collaborateurs_map.items()}


def analyser_declarations_vs_import(ancien_ru_map, collaborateurs_dans_fichier):
    """
    Compare les DERNIÈRES déclarations de départ ('D') et de changement
    d'affectation ('C') de declaration_effectif à l'état du fichier
    collaborateurs qui vient d'être importé.

    - Un départ est considéré "effectué" si le collaborateur n'est plus
      présent dans le nouveau fichier (collaborateurs_dans_fichier).
    - Un changement est considéré "effectué" si l'affectation actuelle du
      collaborateur (après import) correspond à la nouvelle RU déclarée
      (nv_Ru) dans declaration_effectif.

    Seule la dernière déclaration ('D' ou 'C') de chaque collaborateur est
    prise en compte, afin d'éviter les doublons et les déclarations déjà
    obsolètes/remplacées.
    """
    declarations = (
        DeclarationEffectif.objects
        .filter(nature__in=["D", "C"])
        .select_related("collaborateur_it", "nv_Ru")
        .order_by("collaborateur_it_id", "-date", "-id")
    )

    # On ne garde que la dernière déclaration par collaborateur.
    dernieres = {}
    for d in declarations:
        cid = d.collaborateur_it_id
        if cid and cid not in dernieres:
            dernieres[cid] = d

    its_concernes = list(dernieres.keys())
    collaborateurs_apres = {
        c.it: c for c in Collaborateur.objects.filter(it__in=its_concernes)
    }

    departs_non_effectues = []
    changements_non_effectues = []
    effectuees = []

    for cid, decl in dernieres.items():
        collab_actuel = collaborateurs_apres.get(cid)
        collab_declare = decl.collaborateur_it  # peut être None si SET_NULL

        matricule = (collab_actuel.matricule if collab_actuel
                     else getattr(collab_declare, "matricule", None))
        nom_complet = (collab_actuel.nom_complete if collab_actuel
                       else getattr(collab_declare, "nom_complete", cid))

        if decl.nature == "D":
            if cid in collaborateurs_dans_fichier:
                departs_non_effectues.append({
                    "matricule": matricule,
                    "nom_complete": nom_complet,
                    "date_declaration": decl.date,
                    "ru": ancien_ru_map.get(cid),
                    "statut": "Départ déclaré, non effectué",
                })
            else:
                effectuees.append({
                    "matricule": matricule,
                    "nom_complete": nom_complet,
                    "type": "Départ",
                    "date_declaration": decl.date,
                })

        elif decl.nature == "C":
            ancienne_affectation = ancien_ru_map.get(cid)
            nouvelle_declaree = decl.nv_Ru_id
            actuelle_dans_fichier = collab_actuel.ru_it_id if collab_actuel else None

            if actuelle_dans_fichier == nouvelle_declaree:
                effectuees.append({
                    "matricule": matricule,
                    "nom_complete": nom_complet,
                    "type": "Changement d'affectation",
                    "date_declaration": decl.date,
                })
            else:
                changements_non_effectues.append({
                    "matricule": matricule,
                    "nom_complete": nom_complet,
                    "ancienne_affectation": ancienne_affectation,
                    "nouvelle_affectation_declaree": nouvelle_declaree,
                    "affectation_actuelle_fichier": actuelle_dans_fichier,
                    "date_declaration": decl.date,
                    "statut": "Changement déclaré, non effectué",
                })

    return {
        "departs_non_effectues": departs_non_effectues,
        "changements_non_effectues": changements_non_effectues,
        "effectuees": effectuees,
        "nb_departs": len(departs_non_effectues),
        "nb_changements": len(changements_non_effectues),
        "nb_effectuees": len(effectuees),
    }


def _serialiser_synthese(synthese):
    """
    Convertit la synthèse en une structure 100% JSON-sérialisable (les objets
    date ne le sont pas nativement) afin de pouvoir la stocker en base
    (histo_import.synthese_json).
    Elle est ensuite réutilisée pour : (1) réafficher le bouton "Voir la
    synthèse" après un rechargement de page OU une reconnexion, (2) générer
    l'export Excel.
    """
    def _conv_liste(liste):
        out = []
        for item in liste:
            item2 = dict(item)
            if item2.get("date_declaration") is not None:
                item2["date_declaration"] = str(item2["date_declaration"])
            out.append(item2)
        return out

    return {
        "departs_non_effectues": _conv_liste(synthese["departs_non_effectues"]),
        "changements_non_effectues": _conv_liste(synthese["changements_non_effectues"]),
        "effectuees": _conv_liste(synthese["effectuees"]),
        "nb_departs": synthese["nb_departs"],
        "nb_changements": synthese["nb_changements"],
        "nb_effectuees": synthese["nb_effectuees"],
    }


def _derniere_synthese():
    """
    NOUVEAU : récupère la synthèse du DERNIER import collaborateurs qui en
    possède une, directement depuis la base de données (histo_import.synthese_json).

    Contrairement à request.session (qui est propre à un utilisateur/navigateur
    et disparaît au logout ou à l'expiration de la session), cette fonction
    renvoie toujours la même synthèse à tout utilisateur autorisé, qu'il vienne
    de se reconnecter, de changer de navigateur, ou de recharger la page.
    """
    dernier = (
        histo_import.objects
        .exclude(synthese_json__isnull=True)
        .order_by("-date")
        .first()
    )
    return dernier.synthese_json if dernier else None

# ------------------------------------------------------------------
# VUE PRINCIPALE
# ------------------------------------------------------------------
@role_required(['SUPER', "DRH"])
def importer_fichiers_combines(request):
    role = request.session.get('role')
    template_de_base = {
        "SUPER": "utilisateur/navbar_N1.html",
        "DRH": "utilisateur/navbar_N1.html",
    }.get(role, "utilisateur/navbar_N1.html")

    historique_imports = (
        histo_import.objects
        .prefetch_related('details')
        .order_by('-date')[:10]
    )

    if request.method != "POST":
        storage = messages.get_messages(request)
        for _ in storage:
            pass

        # NOUVEAU : la synthèse est lue en base (dernier import avec
        # synthese_json renseigné), donc elle reste disponible même après un
        # logout/reconnexion, un changement de navigateur, etc. Elle ne
        # s'ouvre pas automatiquement dans ce cas (just_imported=False).
        return render(request, "import_data/import.html", {
            "form": MultipleImportForm(),
            "template_de_base": template_de_base,
            "historique_imports": historique_imports,
            "synthese_declarations": _derniere_synthese(),
            "just_imported": False,
        })

    form = MultipleImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "import_data/import.html", {
            "form": form,
            "template_de_base": template_de_base,
            "historique_imports": historique_imports,
            "synthese_declarations": _derniere_synthese(),
            "just_imported": False,
        })

    f_collab = request.FILES.get("fichier_collaborateur")
    f_unite = request.FILES.get("fichier_unite")
    f_dpt = request.FILES.get("fichier_departement")
    f_chg = request.FILES.get("fichier_changement")

    if not (f_collab or f_unite or f_dpt or f_chg):
        messages.error(request, "Veuillez fournir au moins un fichier à importer.", extra_tags="import")
        return render(request, "import_data/import.html", {
            "form": form,
            "template_de_base": template_de_base,
            "historique_imports": historique_imports,
            "synthese_declarations": _derniere_synthese(),
            "just_imported": False,
        })

    erreurs = []
    crees_dpts = crees_unites = crees_collabs = crees_chgs = 0
    import_log = None
    debut = timezone.now()
    synthese_declarations = None
    collaborateurs_dans_fichier = set()

    # OPTIM : une seule lecture de la table Collaborateur, partagée entre
    # importer_departements (résolution HRBP/ADMIN/DRH) et importer_collaborateurs.
    collaborateurs_map = None
    if f_dpt or f_collab:
        collaborateurs_map = {
            c.it: c for c in Collaborateur.objects.only(
                "it", "matricule", "nom_complete", "lot",
                "departement_id", "unite_id", "eq", "shift", "sexe", "ru_it_id",
            )
        }

    # Snapshot de l'affectation AVANT le traitement du nouveau fichier
    # collaborateurs. Sert de point de référence ("ancienne affectation")
    # pour la comparaison avec les déclarations de changement.
    ancien_ru_map = _snapshot_ru(collaborateurs_map) if collaborateurs_map else {}

    if f_dpt:
        try:
            df_dpt = read_uploaded_file(f_dpt)
            crees_dpts = importer_departements(df_dpt, erreurs, collaborateurs_map=collaborateurs_map)
        except Exception as e:
            messages.error(request, f"Erreur de lecture du fichier Départements : {e}", extra_tags="import")

    if f_unite:
        try:
            df_unite = read_uploaded_file(f_unite)
            crees_unites = importer_unites(df_unite, erreurs)
        except Exception as e:
            messages.error(request, f"Erreur de lecture du fichier Unités : {e}", extra_tags="import")

    if f_collab:
        try:
            df_collab = read_uploaded_file(f_collab)
            import_log = histo_import.objects.create(
                utilisateur=request.session.get("it"),
                nom_fichier=f_collab.name,
                statut="EN_COURS",
            )
            crees_collabs, collaborateurs_dans_fichier = importer_collaborateurs(
                df_collab, erreurs, import_log=import_log, collaborateurs_map=collaborateurs_map
            )
            import_log.statut = "SUCCES" if not erreurs else "PARTIEL"
            import_log.save(update_fields=["statut"])

            # Comparaison des dernières déclarations (declaration_effectif)
            # avec l'état réel du fichier qui vient d'être importé.
            synthese_declarations = analyser_declarations_vs_import(
                ancien_ru_map, collaborateurs_dans_fichier
            )

            # NOUVEAU : la synthèse est persistée EN BASE, rattachée à cet
            # import précis (import_log.synthese_json), et non plus dans
            # request.session. Elle reste donc accessible après un logout,
            # une reconnexion, ou depuis un autre poste, pour tout
            # utilisateur SUPER/DRH.
            synthese_serialisee = _serialiser_synthese(synthese_declarations)
            import_log.synthese_json = synthese_serialisee
            import_log.save(update_fields=["synthese_json"])
        except Exception as e:
            if import_log is not None:
                import_log.statut = "ECHEC"
                import_log.save(update_fields=["statut"])
            messages.error(request, f"Erreur de lecture du fichier Collaborateurs : {e}", extra_tags="import")

    if f_chg:
        try:
            df_chg = read_uploaded_file(f_chg)
            crees_chgs = importer_changements(df_chg, erreurs)
        except Exception as e:
            messages.error(request, f"Erreur de lecture du fichier Changements : {e}", extra_tags="import")

    resume = []
    if f_dpt:
        resume.append(f"Départements: {crees_dpts} ligne(s) importée(s) avec succès")
    if f_unite:
        resume.append(f"Unités: {crees_unites} ligne(s) importée(s) avec succès")
    if f_collab:
        detail_msg = f"Collaborateurs: {crees_collabs} ligne(s) traitée(s)"
        if import_log:
            detail_msg += f" ({import_log.depar} créé(s), {import_log.modif} modifié(s), {import_log.supprime} supprimé(s))"
        resume.append(detail_msg)
    if f_chg:
        resume.append(f"Changements d'affectation: {crees_chgs} ligne(s) importée(s) avec succès")

    messages.success(request, "Importation terminée : " + " | ".join(resume), extra_tags="import")

    if erreurs:
        messages.warning(request, f"{len(erreurs)} avertissement(s) : " + " | ".join(erreurs[:5]), extra_tags="import")

    fin = timezone.now()
    # OPTIM / FIX : import_log peut être None si aucun fichier collaborateurs
    # n'a été fourni — on protège l'accès pour éviter l'AttributeError.
    if import_log is not None:
        duree = fin - debut
        import_log.duree = duree.total_seconds()
        import_log.save(update_fields=["duree"])

    historique_imports = (
        histo_import.objects
        .order_by('-date')[:10]
    )

    return render(request, "import_data/import.html", {
        "form": MultipleImportForm(),
        "template_de_base": template_de_base,
        "historique_imports": historique_imports,
        "synthese_declarations": synthese_declarations,
        "just_imported": synthese_declarations is not None,
    })

# ============================================================
# (le reste du fichier : get_collaborateurs_reels, export_effectif_reel — inchangé)
# ============================================================

def get_collaborateurs_reels(departements):
    collaborateurs = (
        Collaborateur.objects
        .filter(departement_id__in=departements)
        .select_related("departement", "unite", "ru_it")
    )

    ids = list(collaborateurs.values_list("it", flat=True))

    declarations = (
        DeclarationEffectif.objects
        .filter(collaborateur_it_id__in=ids, nature__in=["C", "D", "A", "V"])
        .select_related("nv_Ru")
        .order_by("collaborateur_it_id", "-date", "-id")
    )

    dernier_par_collab = {}
    for d in declarations:
        cid = d.collaborateur_it_id
        if cid not in dernier_par_collab:
            dernier_par_collab[cid] = d

    resultats = []
    for c in collaborateurs:
        d = dernier_par_collab.get(c.it)

        if d and d.nature == "D":
            continue

        if d and d.nature == "C" and d.nv_Ru_id:
            ru_affiche = d.nv_Ru_id
        else:
            ru_affiche = c.ru_it_id

        resultats.append({
            "collaborateur": c,
            "ru": ru_affiche,
        })

    return resultats

@role_required(["HRBP", "DRH", "ADMIN", "PILOT"])
def export_effectif_reel(request):
    it = request.session.get("it")
    role = request.session.get("role")

    if role == "HRBP":
        departements_qs = Departement.objects.filter(HRBP_id=it)
    elif role == "DRH":
        departements_qs = Departement.objects.filter(DRH_id=it)
    elif role == "ADMIN":
        departements_qs = Departement.objects.filter(ADMIN_id=it)
    elif role == "PILOT":
        departements_qs = Departement.objects.filter(PILOT_id=it)
    else:
        departements_qs = Departement.objects.none()

    departements = list(departements_qs.values_list("abreviation", flat=True))

    resultats = get_collaborateurs_reels(departements)

    # Build an it -> (matricule, nom_complete) lookup for all RU codes present
    # in the results, so we can display the RU's matricule and full name
    # instead of just their "it" code.
    ru_its = {r["ru"] for r in resultats if r.get("ru")}
    ru_info_map = {
        it_val: (matricule, nom_complete)
        for it_val, matricule, nom_complete in Collaborateur.objects.filter(
            it__in=ru_its
        ).values_list("it", "matricule", "nom_complete")
    }

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Effectif Réel"
    headers = ["Matricule", "IT", "Nom", "Prénom", "Département", "Unité", "Lot", "EP", "RU(utilisateur)","Nom Prénom"]
    ws.append(headers)

    header_fill = PatternFill(start_color="1e293b", end_color="1e293b", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for r in sorted(resultats, key=lambda x: (x["collaborateur"].departement_id or "", x["collaborateur"].nom_complete)):
        c = r["collaborateur"]
        ru_matricule, ru_nom_prenom = ru_info_map.get(r["ru"], ("-", "-")) if r.get("ru") else ("-", "-")

        # 1er mot = Nom, le reste = Prénom
        parts = (c.nom_complete or "").split(None, 1)
        nom = parts[0] if parts else "-"
        prenom = parts[1] if len(parts) > 1 else "-"

        ws.append([
            c.matricule,
            c.it,
            nom,
            prenom,
            c.departement.abreviation if c.departement else "-",
            c.unite_id if c.unite_id else "-",
            c.lot,
            c.eq,
            ru_matricule,
            ru_nom_prenom,
        ])

    for col_cells in ws.columns:
        length = max(len(str(cell.value)) for cell in col_cells if cell.value is not None)
        ws.column_dimensions[col_cells[0].column_letter].width = max(length + 2, 12)

    today_str = timezone.localdate().strftime("%Y-%m-%d")
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="effectif_reel_{today_str}.xlsx"'
    wb.save(response)

    return response


@role_required(['SUPER', "DRH"])
def import_details_json(request, import_id):
    import_log = get_object_or_404(histo_import, pk=import_id)
    details = import_log.details.all().values(
        "action", "it", "matricule", "nom_complete",
        "champ_modifie", "ancienne_valeur", "nouvelle_valeur",
    )
    return JsonResponse({"details": list(details)})


# ------------------------------------------------------------------
# HISTORIQUE COMPLET DES IMPORTS (popup + filtre par date)
# ------------------------------------------------------------------

STATUT_BADGES = {
    "SUCCES": ("Succès", "bg-success-subtle text-success border border-success-subtle"),
    "PARTIEL": ("Partiel", "bg-warning-subtle text-warning border border-warning-subtle"),
    "ECHEC": ("Échec", "bg-danger-subtle text-danger border border-danger-subtle"),
    "EN_COURS": ("En cours", "bg-info-subtle text-info border border-info-subtle"),
}


def _parse_date(val):
    """Parse une date au format YYYY-MM-DD envoyée par un <input type="date">.
    Retourne None si absente ou invalide (le filtre correspondant est alors
    simplement ignoré plutôt que de lever une erreur)."""
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except ValueError:
        return None


@role_required(['SUPER', "DRH"])
def historique_imports_json(request):
    """
    Retourne l'historique COMPLET des imports (pas seulement les 10 derniers),
    avec un filtrage optionnel par plage de dates via les paramètres GET
    `date_debut` et `date_fin` (format YYYY-MM-DD, bornes incluses).
    """
    qs = histo_import.objects.order_by('-date')

    date_debut = _parse_date(request.GET.get("date_debut"))
    date_fin = _parse_date(request.GET.get("date_fin"))

    if date_debut:
        qs = qs.filter(date__gte=datetime.combine(date_debut, time.min))
    if date_fin:
        qs = qs.filter(date__lte=datetime.combine(date_fin, time.max))

    resultats = []
    for item in qs:
        label, css_class = STATUT_BADGES.get(item.statut, (item.statut, "bg-secondary-subtle text-secondary"))
        resultats.append({
            "id": item.id,
            "nom_fichier": item.nom_fichier or "-",
            "utilisateur": item.utilisateur or "Inconnu",
            "duree": item.duree if item.duree is not None else "-",
            "depar": item.depar,
            "modif": item.modif,
            "supprime": item.supprime,
            "date": timezone.localtime(item.date).strftime("%d/%m/%Y %H:%M") if item.date else "-",
            "statut": item.statut,
            "statut_label": label,
            "statut_css": css_class,
        })

    return JsonResponse({"resultats": resultats, "total": len(resultats)})


# ------------------------------------------------------------------
# EXPORT EXCEL DE LA SYNTHÈSE DES DÉCLARATIONS
# ------------------------------------------------------------------

def _style_header_row(ws):
    header_fill = PatternFill(start_color="1e293b", end_color="1e293b", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")


def _autosize(ws):
    for col_cells in ws.columns:
        length = max((len(str(cell.value)) for cell in col_cells if cell.value is not None), default=0)
        ws.column_dimensions[col_cells[0].column_letter].width = max(length + 2, 12)


@role_required(['SUPER', "DRH"])
def export_synthese_declarations(request):
    """
    Exporte en Excel la dernière synthèse des déclarations (générée lors du
    dernier import du fichier collaborateurs), avec 3 feuilles :
    Départs non effectués / Changements non effectués / Déclarations effectuées.

    NOUVEAU : la synthèse est lue depuis la base (histo_import.synthese_json
    du dernier import qui en possède une) au lieu de request.session, afin
    que l'export reste possible même après un logout/reconnexion.
    """
    synthese = _derniere_synthese()

    if not synthese:
        messages.error(
            request,
            "Aucune synthèse disponible à exporter. Veuillez relancer un import de collaborateurs.",
            extra_tags="import",
        )
        return redirect("importer_fichier")

    wb = openpyxl.Workbook()

    # --- Feuille 1 : Départs non effectués ---
    ws1 = wb.active
    ws1.title = "Departs non effectues"
    ws1.append(["Matricule", "Nom complet", "RU", "Date déclaration", "Statut"])
    _style_header_row(ws1)
    for d in synthese.get("departs_non_effectues", []):
        ws1.append([
            d.get("matricule") or "-",
            d.get("nom_complete") or "-",
            d.get("ru") or "-",
            d.get("date_declaration") or "-",
            d.get("statut") or "-",
        ])
    _autosize(ws1)

    # --- Feuille 2 : Changements non effectués ---
    ws2 = wb.create_sheet("Changements non effectues")
    ws2.append([
        "Matricule", "Nom complet", "Ancienne affectation",
        "Nouvelle affectation déclarée", "Affectation actuelle (fichier)", "Date déclaration",
    ])
    _style_header_row(ws2)
    for c in synthese.get("changements_non_effectues", []):
        ws2.append([
            c.get("matricule") or "-",
            c.get("nom_complete") or "-",
            c.get("ancienne_affectation") or "-",
            c.get("nouvelle_affectation_declaree") or "-",
            c.get("affectation_actuelle_fichier") or "-",
            c.get("date_declaration") or "-",
        ])
    _autosize(ws2)

    # --- Feuille 3 : Déclarations effectuées ---
    ws3 = wb.create_sheet("Declarations effectuees")
    ws3.append(["Matricule", "Nom complet", "Type", "Date déclaration"])
    _style_header_row(ws3)
    for e in synthese.get("effectuees", []):
        ws3.append([
            e.get("matricule") or "-",
            e.get("nom_complete") or "-",
            e.get("type") or "-",
            e.get("date_declaration") or "-",
        ])
    _autosize(ws3)

    today_str = timezone.localdate().strftime("%Y-%m-%d")
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="synthese_declarations_{today_str}.xlsx"'
    wb.save(response)

    return response