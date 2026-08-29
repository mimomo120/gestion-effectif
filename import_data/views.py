import pandas as pd
from django.shortcuts import render
from django.contrib import messages
from django.db import transaction
from .forms import MultipleImportForm
from Collaborateur.models import Collaborateur, Departement, Unite

# ------------------------------------------------------------------
# FONCTIONS AUXILIAIRES SIMPLIFIÉES
# ------------------------------------------------------------------

def clean_val(val):
    """Convertit les valeurs Pandas (NaN, 1467.0) en chaîne propre ou None."""
    if val is None or pd.isna(val):
        return None
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


def clean_int(val, default=0):
    """Convertit proprement en entier."""
    try:
        if pd.isna(val) or val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


def read_uploaded_file(fichier):
    """Lit un fichier Excel ou CSV."""
    if fichier.name.endswith(".csv"):
        df = pd.read_csv(fichier)
    else:
        df = pd.read_excel(fichier)
    df.columns = df.columns.str.strip()
    return df


# ------------------------------------------------------------------
# IMPORT DÉPARTEMENTS (optimisé)
# ------------------------------------------------------------------

def importer_departements(df_dpt, erreurs):
    """Traite le DataFrame des départements de façon optimisée (peu de requêtes)."""
    crees = 0

    # Préchargement des collaborateurs pouvant être HRBP/ADMIN/DRH (1 seule requête)
    collaborateurs_map = {c.it: c for c in Collaborateur.objects.all()}
    departements_existants = {d.abreviation: d for d in Departement.objects.all()}

    a_creer = []
    a_maj = []

    for i, row in df_dpt.iterrows():
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
                ADNMIN=admin_obj,
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

            crees += 1
        except Exception as e:
            erreurs.append(f"Départements Ligne {i+2}: {e}")

    with transaction.atomic():
        if a_creer:
            Departement.objects.bulk_create(a_creer, batch_size=500)
        if a_maj:
            Departement.objects.bulk_update(
                a_maj, ["nom_departement", "HRBP", "ADNMIN", "DRH", "maquette"], batch_size=500
            )

    return crees


# ------------------------------------------------------------------
# IMPORT UNITÉS (optimisé)
# ------------------------------------------------------------------

def importer_unites(df_unite, erreurs):
    """Traite le DataFrame des unités de façon optimisée (peu de requêtes)."""
    crees = 0

    unites_existantes = {u.abreviation: u for u in Unite.objects.all()}
    a_creer = []
    a_maj = []

    for i, row in df_unite.iterrows():
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

            crees += 1
        except Exception as e:
            erreurs.append(f"Unités Ligne {i+2}: {e}")

    with transaction.atomic():
        if a_creer:
            Unite.objects.bulk_create(a_creer, batch_size=500)
        if a_maj:
            Unite.objects.bulk_update(a_maj, ["nom", "maquette", "A", "T", "P", "C"], batch_size=500)

    return crees


# ------------------------------------------------------------------
# IMPORT COLLABORATEURS (optimisé — le plus gros volume)
# ------------------------------------------------------------------

def importer_collaborateurs(df_collab, erreurs):
    """Traite le DataFrame des collaborateurs de façon optimisée (peu de requêtes)."""
    crees = 0

    # --- Préchargement de TOUTES les données de référence en mémoire (4 requêtes au total) ---
    departements_map = {d.abreviation: d for d in Departement.objects.all()}
    unites_map = {u.abreviation: u for u in Unite.objects.all()}
    collaborateurs_map = {c.it: c for c in Collaborateur.objects.all()}
    # Map matricule -> it, pour le cas où la colonne "RU" du fichier contient un matricule
    # plutôt qu'un code "it" (les deux formats sont gérés automatiquement).
    collaborateurs_par_matricule = {
        c.matricule: c.it for c in collaborateurs_map.values() if c.matricule
    }

    a_creer = []
    a_maj = []
    ru_a_resoudre = []  # (it_du_collab, valeur_ru_brute_du_fichier) à résoudre après création/mise à jour

    for i, row in df_collab.iterrows():
        try:
            utilisateur_it = clean_val(row.get("Utilisateur"))
            if not utilisateur_it:
                erreurs.append(f"Collaborateurs Ligne {i+2}: Identifiant Utilisateur manquant.")
                continue

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
                shift=row.get("Shift"),
                sexe=clean_int(row.get("Sexe"), default=1),
            )

            matricule_val = data["matricule"]

            if utilisateur_it in collaborateurs_map:
                obj = collaborateurs_map[utilisateur_it]
                for k, v in data.items():
                    setattr(obj, k, v)
                a_maj.append(obj)
            else:
                obj = Collaborateur(it=utilisateur_it, **data)
                a_creer.append(obj)
                collaborateurs_map[utilisateur_it] = obj  # pour que les lignes suivantes le retrouvent

            # On garde aussi la map matricule -> it à jour pour ce nouveau/mis à jour collaborateur,
            # au cas où il serait lui-même référencé comme RU par une autre ligne du fichier.
            if matricule_val:
                collaborateurs_par_matricule[matricule_val] = utilisateur_it

            if ru_mat:
                ru_a_resoudre.append((utilisateur_it, ru_mat))

            crees += 1
        except Exception as e:
            erreurs.append(f"Collaborateurs Ligne {i+2}: {e}")

    # --- Écriture en base : requêtes groupées au lieu d'une par ligne ---
    with transaction.atomic():
        if a_creer:
            Collaborateur.objects.bulk_create(a_creer, batch_size=500)
        if a_maj:
            Collaborateur.objects.bulk_update(
                a_maj,
                ["matricule", "nom_complete", "lot", "departement", "unite", "eq", "shift", "sexe"],
                batch_size=500,
            )

    # --- Résolution des RU en une seule passe, après que tous les collaborateurs existent ---
    # (gère le cas où le RU est plus bas dans le fichier, ou créé dans le même import)
    if ru_a_resoudre:
        tous_les_collabs = {
            c.it: c for c in Collaborateur.objects.filter(it__in=[u for u, _ in ru_a_resoudre])
        }

        a_maj_ru = []
        for utilisateur_it, ru_val in ru_a_resoudre:
            collab = tous_les_collabs.get(utilisateur_it)
            if collab is None:
                continue

            # 1er essai : la valeur du fichier est directement un code "it" existant
            ru_it_resolu = ru_val if ru_val in collaborateurs_map else None

            # 2e essai (fallback) : la valeur du fichier est un matricule -> on retrouve son "it"
            if not ru_it_resolu:
                ru_it_resolu = collaborateurs_par_matricule.get(ru_val)

            if ru_it_resolu:
                collab.ru_it_id = ru_it_resolu  # assignation directe de l'ID, pas de requête supplémentaire
                a_maj_ru.append(collab)
            else:
                erreurs.append(
                    f"RU '{ru_val}' introuvable (ni comme 'it', ni comme matricule) pour le collaborateur '{utilisateur_it}'."
                )

        if a_maj_ru:
            Collaborateur.objects.bulk_update(a_maj_ru, ["ru_it"], batch_size=500)

    return crees


# ------------------------------------------------------------------
# VUE PRINCIPALE
# ------------------------------------------------------------------

def importer_fichiers_combines(request):
    role = request.session.get('role')
    template_de_base = {
        "HRBP": "declaration_effectif/HRBP/navbar.html",
        "ADMIN": "declaration_effectif/Admin/navbar.html",
    }.get(role, "declaration_effectif/Super/navbar.html")

    if request.method != "POST":
        return render(request, "import_data/import.html", {
            "form": MultipleImportForm(),
            "template_de_base": template_de_base,
        })

    form = MultipleImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "import_data/import.html", {"form": form, "template_de_base": template_de_base})

    f_collab = request.FILES.get("fichier_collaborateur")
    f_unite = request.FILES.get("fichier_unite")
    f_dpt = request.FILES.get("fichier_departement")

    if not (f_collab or f_unite or f_dpt):
        messages.error(request, "Veuillez fournir au moins un fichier à importer.")
        return render(request, "import_data/import.html", {"form": form, "template_de_base": template_de_base})

    erreurs = []
    crees_dpts = crees_unites = crees_collabs = 0

    # --------------------------------------------------------------
    # 1. IMPORT DES DÉPARTEMENTS (avant les collaborateurs : ils y font référence)
    # --------------------------------------------------------------
    if f_dpt:
        try:
            df_dpt = read_uploaded_file(f_dpt)
            crees_dpts = importer_departements(df_dpt, erreurs)
        except Exception as e:
            messages.error(request, f"Erreur de lecture du fichier Départements : {e}")

    # --------------------------------------------------------------
    # 2. IMPORT DES UNITÉS (avant les collaborateurs : ils y font référence)
    # --------------------------------------------------------------
    if f_unite:
        try:
            df_unite = read_uploaded_file(f_unite)
            crees_unites = importer_unites(df_unite, erreurs)
        except Exception as e:
            messages.error(request, f"Erreur de lecture du fichier Unités : {e}")

    # --------------------------------------------------------------
    # 3. IMPORT DES COLLABORATEURS
    # --------------------------------------------------------------
    if f_collab:
        try:
            df_collab = read_uploaded_file(f_collab)
            crees_collabs = importer_collaborateurs(df_collab, erreurs)
        except Exception as e:
            messages.error(request, f"Erreur de lecture du fichier Collaborateurs : {e}")

    # --------------------------------------------------------------
    # MESSAGES DE RETOUR
    # --------------------------------------------------------------
    resume = []
    if f_dpt: resume.append(f"{crees_dpts} départements")
    if f_unite: resume.append(f"{crees_unites} unités")
    if f_collab: resume.append(f"{crees_collabs} collaborateurs")

    messages.success(request, f"Importation terminée : {', '.join(resume)} traité(s).")
    if erreurs:
        messages.warning(request, f"{len(erreurs)} avertissement(s) : " + " | ".join(erreurs[:5]))

    return render(request, "import_data/import.html", {
        "form": MultipleImportForm(),
        "template_de_base": template_de_base,
    })