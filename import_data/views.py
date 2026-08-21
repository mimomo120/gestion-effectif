import pandas as pd
from django.shortcuts import render
from django.contrib import messages
from django.db import transaction, IntegrityError

from .forms import MultipleImportForm
from Collaborateur.models import Collaborateur, Departement, Unite
from declaration_effectif.models import historique


# ==========================================
# FONCTIONS UTILITAIRES DE NETTOYAGE
# ==========================================

def clean_id(value):
    """Nettoie matricule/RU/Utilisateur lus par pandas (évite '1467.0')."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def clean_int(value, default=None):
    """Nettoie un compteur numérique lu par pandas (NaN/float -> int)."""
    if value is None or pd.isna(value):
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_or_create_departement(dpt_code):
    """Récupère ou crée un département par son abréviation."""
    dpt_code = str(dpt_code).strip()
    departement_obj = Departement.objects.filter(abreviation=dpt_code).first()
    if departement_obj:
        return departement_obj
    try:
        return Departement.objects.create(abreviation=dpt_code, nom_departement=dpt_code)
    except IntegrityError:
        return Departement.objects.filter(abreviation=dpt_code).first()


def get_unite(unite_code):
    """Retrouve une Unité existante par son abréviation."""
    if not unite_code or pd.isna(unite_code):
        return None
    return Unite.objects.filter(abreviation=str(unite_code).strip()).first()


def read_uploaded_file(fichier):
    """Lit un fichier Excel ou CSV via Pandas et nettoie les entêtes."""
    if fichier.name.endswith(".csv"):
        df = pd.read_csv(fichier)
    else:
        df = pd.read_excel(fichier)
    df.columns = df.columns.str.strip()
    return df.where(pd.notnull(df), None)


# ==========================================
# VUE COMBINÉE D'IMPORTATION
# ==========================================

def importer_fichiers_combines(request):
    """Importe simultanément le fichier Unités et le fichier Collaborateurs."""
    if request.method == "POST":
        form = MultipleImportForm(request.POST, request.FILES)
        if form.is_valid():
            f_collab = request.FILES["fichier_collaborateur"]
            f_unite = request.FILES["fichier_unite"]

            # 1. Lecture des deux fichiers
            try:
                df_unite = read_uploaded_file(f_unite)
                df_collab = read_uploaded_file(f_collab)
            except Exception as e:
                messages.error(request, f"Impossible de lire les fichiers : {e}")
                return render(request, "import_data/import.html", {"form": form})

            # 2. Vérification des colonnes requises dans le fichier Collaborateurs
            req_collab = ["Utilisateur", "Nom", "Prénom", "Lot"]
            missing = [c for c in req_collab if c not in df_collab.columns]
            if missing:
                messages.error(request, f"Collaborateurs - Colonnes manquantes : {', '.join(missing)}")
                return render(request, "import_data/import.html", {"form": form})

            erreurs = []
            crees_unites = 0
            crees_collabs = 0

            # 3. Transaction globale (les Unités sont traitées avant les Collaborateurs)
            with transaction.atomic():

                # --- A. TRAITEMENT DU FICHIER UNITES ---
                for i, row in df_unite.iterrows():
                    sid = transaction.savepoint()
                    try:
                        abbrev = row.get("abreviation")
                        if not abbrev:
                            erreurs.append(f"Unités Ligne {i+2}: abréviation manquante")
                            transaction.savepoint_rollback(sid)
                            continue

                        existing = Unite.objects.filter(abreviation=abbrev).first()

                        def val_or_exist(key, old_val):
                            v = clean_int(row.get(key), default=None)
                            if v is not None:
                                return v
                            return old_val if old_val is not None else 0

                        Unite.objects.update_or_create(
                            abreviation=abbrev,
                            defaults={
                                "nom": row.get("nom") or abbrev,
                                "maquette": val_or_exist("maquette", existing.maquette if existing else None),
                                "A": val_or_exist("A", existing.A if existing else None),
                                "T": val_or_exist("T", existing.T if existing else None),
                                "O": val_or_exist("O", existing.O if existing else None),
                                "P": val_or_exist("P", existing.P if existing else None),
                                "C": val_or_exist("C", existing.C if existing else None),
                            }
                        )
                        transaction.savepoint_commit(sid)
                        crees_unites += 1
                    except Exception as e:
                        transaction.savepoint_rollback(sid)
                        erreurs.append(f"Unités Ligne {i+2}: {str(e)[:80]}")

                # --- B. TRAITEMENT DU FICHIER COLLABORATEURS ---
                for i, row in df_collab.iterrows():
                    sid = transaction.savepoint()
                    try:
                        utilisateur = clean_id(row.get("Utilisateur"))
                        if not utilisateur:
                            erreurs.append(f"Collabs Ligne {i+2}: Utilisateur manquant")
                            transaction.savepoint_rollback(sid)
                            continue

                        # Département
                        dpt_code = row.get("DPT")
                        departement_obj = get_or_create_departement(dpt_code) if dpt_code else None

                        # Unité
                        unite_code = row.get("Unite")
                        unite_obj = None
                        if unite_code is not None and not pd.isna(unite_code):
                            unite_obj = get_unite(unite_code)
                            if not unite_obj:
                                erreurs.append(f"Collabs Ligne {i+2}: Unité {unite_code} introuvable")

                        # Determination du poste & matricule
                        lot = row.get("Lot", "")
                        post = "T" if lot == "P" else ("O" if lot in ("O", "A") else None)
                        matricule = clean_id(row.get("Matricule"))

                        # RU
                        ru_mat = clean_id(row.get("RU"))
                        ru_obj = Collaborateur.objects.filter(matricule=ru_mat).first() if ru_mat else None

                        nom = row.get("Nom") or ""
                        prenom = row.get("Prénom") or ""

                        Collaborateur.objects.update_or_create(
                            it=utilisateur,
                            defaults={
                                "matricule": matricule,
                                "nom_complete": f"{nom} {prenom}".strip(),
                                "lot": lot,
                                "departement": departement_obj,
                                "eq": row.get("Equipe", ""),
                                "shift": row.get("Shift"),
                                "sexe": row.get("Sexe", ""),
                                "post": post,
                                "unite": unite_obj,
                                "ru_it": ru_obj,
                            }
                        )
                        transaction.savepoint_commit(sid)
                        crees_collabs += 1
                    except Exception as e:
                        transaction.savepoint_rollback(sid)
                        erreurs.append(f"Collabs Ligne {i+2}: {str(e)[:80]}")

            # 4. Feedback utilisateur
            messages.success(
                request, 
                f"Importation terminée avec succès : {crees_unites} unités et {crees_collabs} collaborateurs traités."
            )
            if erreurs:
                messages.error(request, f"{len(erreurs)} avertissement(s) : " + " | ".join(erreurs[:5]))

            return render(request, "import_data/import.html", {"form": MultipleImportForm()})
    else:
        form = MultipleImportForm()

    return render(request, "import_data/import.html", {"form": form})