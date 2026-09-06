from django.conf.locale import it
from django.shortcuts import render, redirect , get_object_or_404
from utilisateur.models import utilisateur
from Collaborateur.models import Departement , Collaborateur ,Unite
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q,Count,Sum ,Max
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif ,Alert ,historique
from django.http import JsonResponse
from datetime import date
import json
from datetime import date , datetime , timedelta
from django.db import transaction, IntegrityError
from django.views.decorators.csrf import ensure_csrf_cookie
from Collaborateur.views import (
    rec, Ru_Rg, liste_N1_par_N2, Rg_Dur, liste_N1_pr_N3, liste_N3_N4,
    SystEff, reelEff, get_tous_les_it_sous, get_tous_les_n1,
    get_n1_et_n2_sous, get_managers_its, invalider_cache_hierarchie,
)
from django.views.decorators.http import require_POST
from utilisateur.decorators import role_required
from django.utils.dateparse import parse_date
from django.core.paginator import PageNotAnInteger, Paginator, EmptyPage
import bisect
from django.views.decorators.cache import never_cache

@ensure_csrf_cookie

# ============================================================
# Valider la liste des operateurs
# ============================================================
def valider(request):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON invalide"}, status=400)

    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "Session expirée."}, status=401)

    liste_valides = data.get("valides", [])
    liste_refuses = data.get("changement",[])
    liste_depart =  data.get("depart", [])
    liste_ajouter = data.get("ajouter", [])
    try:
        with transaction.atomic():
            for operateur in liste_valides:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get((Q(it=operateur))), Ru_id=it,
                nature="V")
            for operateur in liste_refuses:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get(it=operateur["it"]),  Ru_id=it,
                nv_Ru=Collaborateur.objects.get(it=operateur["nvRu"]), nature="C"
            )
            for operateur in liste_depart:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get(it=operateur),  Ru_id=it, nature="D"
            )
            for operateur in liste_ajouter:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get(it=operateur), Ru_id=it,
                nature="A"
            )
    except (IntegrityError, KeyError, Collaborateur.DoesNotExist) as e:
        return JsonResponse({"error": "Erreur lors de l'enregistrement.", "status": "erreur"}, status=400)

    # FIX PERF : les caches (managers_its, hierarchie_map, ru_reel_et_departs)
    # sont maintenant potentiellement obsolètes puisqu'on vient de créer des
    # déclarations qui peuvent changer l'effectif réel. On les invalide.
    invalider_cache_hierarchie()

    return JsonResponse({"status": "valider"})

# ============================================================
#fct pr rederiger vers la page validation + liste des operateurs
# ============================================================

@role_required('N+1')
def validation_view(request):
    it = request.session.get("it")
    if not it:
        return render(request, 'declaration_effectif/N1/Validation.html', {
            "operateurs_finaux": [], "nbr": 0, "status": False, "date": timezone.localdate()
        })

    directs_qs = Collaborateur.objects.filter(ru_it__it=it).exclude(it=it)
    direct_ids = list(directs_qs.values_list('it', flat=True))

    manager_direct_ids = set(
        Collaborateur.objects.filter(ru_it_id__in=direct_ids)
        .values_list('ru_it_id', flat=True)
        .distinct()
    )

    manager_direct_its = set(
        Collaborateur.objects.filter(it__in=manager_direct_ids)
        .values_list('it', flat=True)
    )

    aujourdhui = timezone.localdate()
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()

    if der and der.date == aujourdhui:
        operateurs_finaux_qs = declaration_effectif.objects.filter(Ru_id=it, date=aujourdhui)
        operateurs_finaux_qs = operateurs_finaux_qs.exclude(collaborateur_it_id__in=manager_direct_its)
        status = True
        operateurs_finaux = operateurs_finaux_qs.exclude(collaborateur_it__lot__in=["C", "E"])
    else:
        candidats = rec(request)

        if hasattr(candidats, 'exclude'):
            operateurs_finaux = candidats.exclude(it__in=manager_direct_its)
        else:
            operateurs_finaux = [c for c in candidats if getattr(c, 'it', None) not in manager_direct_its]
        status = False
    if hasattr(operateurs_finaux, 'count'):
        nbr = operateurs_finaux.count()
    else:
        nbr = len(operateurs_finaux)

    return render(
        request,
        'declaration_effectif/N1/Validation.html',
        {
            "operateurs_finaux": operateurs_finaux,
            "nbr": nbr,
            "status": status,
            "date": aujourdhui
        }
    )

# ============================================================
# return ecart S/R et R/S
# ============================================================
def difference(request):
    it = request.session.get("it")

    operateur_systeme = SystEff(it)
    liste_s = set(operateur_systeme.values_list("it", flat=True))

    operateur_reel = reelEff(it)
    if  operateur_reel :
        liste_r = set(
            operateur_reel.values_list("it", flat=True)
        )
        r=liste_r - liste_s
        s=liste_s - liste_r
        reel = Collaborateur.objects.filter(it__in=r)
        systeme = Collaborateur.objects.filter(it__in=s)

        return {
            "systeme1": systeme,
            "reel1": reel,
        }

    return {
        "systeme1":Collaborateur.objects.none(),
        "reel1": Collaborateur.objects.none(),
    }

# ============================================================
# recupere la liste des affectations d'un collaborateur
# ============================================================
def histo(ut):
    if not ut:
        return {"declarations": [], "nbr": 0}

    resp = Collaborateur.objects.filter(it=ut).first()

    if not resp:
        return {"declarations": [], "nbr": 0}

    declarations = historique.objects.filter(
        Q(initial=resp.nom_complete) & ~Q(etat="Terminé")
    )
    nbr = declarations.count()

    return {"declarations": declarations, "nbr": nbr}


def historique_pour(liste_its):
    """
    FIX PERF : remplace la boucle qui appelait histo() (donc plusieurs
    requêtes) pour CHAQUE manager de la liste, par une seule requête
    groupée sur les noms de tous les managers concernés.
    """
    liste_its = list(liste_its)
    if not liste_its:
        return historique.objects.none()

    noms = set(
        Collaborateur.objects.filter(it__in=liste_its).values_list("nom_complete", flat=True)
    )
    if not noms:
        return historique.objects.none()

    return historique.objects.filter(initial__in=noms).exclude(etat="Terminé")


# ============================================================
# rederiger vers la page des affectations avec  les affectations
# ============================================================
@role_required('N+1')
def histo_aff(request):
    util = request.session.get("it")
    changements = histo(util)
    declarations = changements["declarations"]

    status = request.GET.get("status", "all")

    if status == "valide":
        declarations = declarations.filter(etat__icontains="valid")
    elif status == "refuse":
        declarations = declarations.filter(etat__icontains="refus")

    paginator = Paginator(declarations, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    context = {
        "info": page_obj,
        "page_obj": page_obj,
        "nbr": paginator.count,
        "status": status,
    }
    return render(
        request,
        "declaration_effectif/N1/Affectations_historique.html",
        context,
    )
# ============================================================
# Supprimer la declaration effectuers ds le jour meme
# ============================================================
def supprimer(request):
    it = request.session.get("it")

    if not it:
        return JsonResponse({"status": "erreur", "error": "Session invalide"}, status=401)

    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()

    if der:
        declaration_effectif.objects.filter(Ru_id=it, date=der.date).delete()
        invalider_cache_hierarchie()
        return JsonResponse({"status": "supprimer"})

    return JsonResponse({"status": "erreur", "error": "Aucune déclaration à supprimer"}, status=404)

# ============================================================
# rederiger vers la page des affectations avec  les affectations
# ============================================================
def afficher_modifier(request):
    it = request.session.get("it")
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    aujourdhui = timezone.localdate()

    if der and der.date == aujourdhui:
        return JsonResponse({"valide": True})
    return JsonResponse({"valide": False})


# ============================================================
# rederiger vers la page des respo N+1 ss validation
# ============================================================
@role_required('N+2')
def liste_N1_non_valides_N2(request):
    maint = timezone.localdate()
    it=request.session.get("it")
    liste_ru = Ru_Rg(it)
    liste_n1_ids=set(liste_ru.values_list("it",flat=True))

    declaration = declaration_effectif.objects.filter(date=maint, Ru_id__in=liste_n1_ids)
    liste_Ru = set(declaration.values_list("Ru_id", flat=True))

    liste_ru_avec_operateurs = set(
            Collaborateur.objects.filter(ru_it_id__in=liste_n1_ids)
            .values_list("ru_it_id", flat=True)
            .distinct())

    ru_non_valides_ids = liste_ru_avec_operateurs - liste_Ru

    non_valides = liste_ru.filter(it__in=ru_non_valides_ids).distinct()

    return render(
        request,
        "declaration_effectif/N2/validation.html",
        {"non_valides": non_valides,"date": maint},
    )


# ============================================================
# rederiger vers la page des affectations de N+2 et c'est N+1
# ============================================================
@role_required('N+2')
def affectation_N1(request):
    util = request.session.get("it")

    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "").strip()
    ru_acceuil = request.GET.get("ru_acceuil", "").strip()

    changements = histo(util)
    toutes_declarations_N2 = list(changements["declarations"])

    n1 = Ru_Rg(util)
    n1_ids = list(n1.values_list("it", flat=True))
    # FIX PERF : une seule requête groupée au lieu d'une par N+1
    toutes_declarations = list(historique_pour(n1_ids))

    tab_actif = request.GET.get("tab", "tab-mes")
    ensemble_onglet = toutes_declarations if tab_actif == "tab-toutes" else toutes_declarations_N2
    ru_initiaux = sorted({d.initial for d in ensemble_onglet if d.initial})
    ru_acceuils = sorted({d.acceuil for d in ensemble_onglet if d.acceuil})

    if status == "valide":
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.etat and "valid" in str(d.etat).lower()]
        toutes_declarations = [d for d in toutes_declarations if d.etat and "valid" in str(d.etat).lower()]
    elif status == "refuse":
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.etat and "refus" in str(d.etat).lower()]
        toutes_declarations = [d for d in toutes_declarations if d.etat and "refus" in str(d.etat).lower()]
    elif status == "non_demarrer":
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.etat and "non démarr" in str(d.etat).lower()]
        toutes_declarations = [d for d in toutes_declarations if d.etat and "non démarr" in str(d.etat).lower()]

    if ru_init:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.initial == ru_init]
        toutes_declarations = [d for d in toutes_declarations if d.initial == ru_init]

    if ru_acceuil:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.acceuil == ru_acceuil]
        toutes_declarations = [d for d in toutes_declarations if d.acceuil == ru_acceuil]

    total_nbr2 = len(toutes_declarations_N2)
    total_nbr = len(toutes_declarations)

    paginator_n1 = Paginator(toutes_declarations, 10)
    page_n1 = request.GET.get("page_n1", 1)
    page_obj_n1 = paginator_n1.get_page(page_n1)

    paginator_n2 = Paginator(toutes_declarations_N2, 10)
    page_n2 = request.GET.get("page_n2", 1)
    page_obj_n2 = paginator_n2.get_page(page_n2)

    context = {
        "n2": page_obj_n2,
        "page_obj_n2": page_obj_n2,
        "info": page_obj_n1,
        "page_obj_n1": page_obj_n1,
        "nbr": total_nbr,
        "nbr2": total_nbr2,
        "status": status,
        "ru_init": ru_init,
        "ru_acceuil": ru_acceuil,
        "ru_initiaux": ru_initiaux,
        "ru_acceuils": ru_acceuils,
    }

    return render(request, "declaration_effectif/N2/affectation.html", context)

# ============================================================
# rederiger vers la page des respo N+1 ss validation pr N+3
# ============================================================
@role_required('N+3')
def liste_N1_non_valides_N3(request):
    maint = timezone.localdate()
    it = request.session.get("it")

    n1_ids = get_tous_les_n1(it)

    declaration = declaration_effectif.objects.filter(
        date=maint,
        Ru_id__in=n1_ids,
    )
    liste_declares = set(declaration.values_list("Ru_id", flat=True))

    non_valides = Collaborateur.objects.filter(
        it__in=n1_ids
    ).exclude(it__in=liste_declares)

    return render(
        request,
        "declaration_effectif/N3/validation.html",
        {"non_valides": non_valides, "date": maint}
    )


# ============================================================
# rederiger vers la page des affectations de N+2 et c'est N+1 ,N+2
# ============================================================
@role_required('N+3')
def affectation_N3(request):
    util = request.session.get("it")
    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "").strip()
    ru_acceuil = request.GET.get("ru_acceuil", "").strip()

    n1_ids, n2_ids = get_n1_et_n2_sous(util)

    # FIX PERF : historique_pour() fait maintenant UNE requête par
    # ensemble (n2_ids, n1_ids) au lieu d'une par manager.
    toutes_declarations_N2 = list(historique_pour(n2_ids))
    toutes_declarations = list(historique_pour(n1_ids))

    tab_actif = request.GET.get("tab", "tab-mes")
    ensemble_onglet = toutes_declarations if tab_actif == "tab-toutes" else toutes_declarations_N2
    ru_initiaux = sorted({d.initial for d in ensemble_onglet if d.initial})
    ru_acceuils = sorted({d.acceuil for d in ensemble_onglet if d.acceuil})

    def filtrer(liste, mot_cle):
        return [d for d in liste if d.etat and mot_cle in str(d.etat).lower()]

    if status == "valide":
        toutes_declarations_N2 = filtrer(toutes_declarations_N2, "valid")
        toutes_declarations = filtrer(toutes_declarations, "valid")
    elif status == "refuse":
        toutes_declarations_N2 = filtrer(toutes_declarations_N2, "refus")
        toutes_declarations = filtrer(toutes_declarations, "refus")
    elif status == "non_demarrer":
        toutes_declarations_N2 = filtrer(toutes_declarations_N2, "non démarr")
        toutes_declarations = filtrer(toutes_declarations, "non démarr")

    if ru_init:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.initial == ru_init]
        toutes_declarations = [d for d in toutes_declarations if d.initial == ru_init]

    if ru_acceuil:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.acceuil == ru_acceuil]
        toutes_declarations = [d for d in toutes_declarations if d.acceuil == ru_acceuil]

    total_nbr2 = len(toutes_declarations_N2)
    total_nbr = len(toutes_declarations)

    paginator_n1 = Paginator(toutes_declarations, 10)
    page_n1 = request.GET.get("page_n1", 1)
    page_obj_n1 = paginator_n1.get_page(page_n1)

    paginator_n2 = Paginator(toutes_declarations_N2, 10)
    page_n2 = request.GET.get("page_n2", 1)
    page_obj_n2 = paginator_n2.get_page(page_n2)

    context = {
        "n2": page_obj_n2,
        "page_obj_n2": page_obj_n2,
        "info": page_obj_n1,
        "page_obj_n1": page_obj_n1,
        "nbr": total_nbr,
        "nbr2": total_nbr2,
        "status": status,
        "ru_init": ru_init,
        "ru_acceuil": ru_acceuil,
        "ru_initiaux": ru_initiaux,
        "ru_acceuils": ru_acceuils,
    }

    return render(request, "declaration_effectif/N2/affectation.html", context)
# ============================================================
# fct pr engregistrer les alerts envoier
# ============================================================
def envoyer_alert(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Requête invalide."}, status=400)

    ru_it = data.get("ru_it")
    it = request.session.get("it")

    if not ru_it:
        return JsonResponse({"status": "error", "message": "Matricule du RU manquant."}, status=400)
    if not it:
        return JsonResponse({"status": "error", "message": "Utilisateur non authentifié."}, status=401)

    emetteur_obj = get_object_or_404(Collaborateur, it=it)
    recepteur_obj = get_object_or_404(Collaborateur, it=ru_it)

    Alert.objects.create(
        emetteur=emetteur_obj,
        recepteur=recepteur_obj,
        contenu="Vous n'avez pas déclaré votre liste des effectifs pour aujourd'hui.",
        lu=0
    )

    return JsonResponse({"status": "ok", "message": "Alerte envoyée."})

# ============================================================
# # rederiger vers dashboard de N+3
# ============================================================

def _collecter_n1_feuilles(managers, tous_ru_it):
    resultat = []
    for m in managers:
        sous_directs = Collaborateur.objects.filter(ru_it_id=m.it)
        sous_managers = [c for c in sous_directs if c.it in tous_ru_it]
        if sous_managers:
            resultat.extend(_collecter_n1_feuilles(sous_managers, tous_ru_it))
        else:
            resultat.append(m)
    return resultat

@role_required('N+3')
def dashboard_N3(request):
    it_session_original = request.session.get("it")
    if not it_session_original:
        return redirect("login")

    # FIX PERF : calculé une seule fois via le cache, plutôt qu'une
    # requête complète sur toute la table Collaborateur ici même.
    tous_ru_it = get_managers_its()

    directs_n3 = Collaborateur.objects.filter(ru_it_id=it_session_original).select_related('unite')
    managers_directs_n3 = [c for c in directs_n3 if c.it in tous_ru_it]
    operateurs_directs_n3 = [c for c in directs_n3 if c.it not in tous_ru_it]

    n1_directs = []
    n2_list = []
    n2_to_n1 = {}
    n2_to_operateurs = {}

    for m in managers_directs_n3:
        sous_directs = Collaborateur.objects.filter(ru_it_id=m.it)
        sous_managers = [c for c in sous_directs if c.it in tous_ru_it]
        sous_operateurs = [c for c in sous_directs if c.it not in tous_ru_it]
        if sous_managers:
            n2_list.append(m)
            n2_to_n1[m.it] = _collecter_n1_feuilles(sous_managers, tous_ru_it)
            n2_to_operateurs[m.it] = sous_operateurs
        else:
            n1_directs.append(m)

    it_n2_set = {n2.it for n2 in n2_list}
    n1_directs = [c for c in n1_directs if c.it not in it_n2_set]
    for n2_it, sous_n1 in n2_to_n1.items():
        n2_to_n1[n2_it] = [c for c in sous_n1 if c.it not in it_n2_set]

    it_n1_set = set(c.it for c in n1_directs)
    for sous_managers in n2_to_n1.values():
        it_n1_set.update(c.it for c in sous_managers)
    it_n1 = list(it_n1_set)

    nbr_n1_total = len(it_n1)
    nbr_n2_total = len(n2_list)
    it_n2 = [n2.it for n2 in n2_list]

    total_operateurs_directs_n2 = sum(len(v) for v in n2_to_operateurs.values())

    if not it_n1 and not operateurs_directs_n3 and not total_operateurs_directs_n2:
        return render(request, "declaration_effectif/N3/dashboard.html", {
            "liste_ru_stats": [], "total_r": 0, "total_syst": 0, "MR": 0, "MS": 0,
            "maquette_totale": 0, "maint": timezone.localdate(),
            "non_valides": 0,
            "operateurs_directs_n3": 0,
            "n1_directs": [], "n2_list": [], "nbr_n1_total": 0, "nbr_n2_total": 0,
            "chart_labels": [], "chart_data": [],
            "labels_ru": [], "data_reel": [], "data_systeme": [], "data_maquette": [],
            "lot_labels": [], "lot_reel": [], "lot_systeme": [], "lot_maquette": [],
            "lot_details": {},
        })

    today = timezone.now().date()
    start = today - timedelta(days=6)
    dates = [start + timedelta(days=i) for i in range(7)]
    labels_list = [d.strftime('%d %b') for d in dates]

    collaborateurs_n1 = Collaborateur.objects.filter(it__in=it_n1).exclude(it__in=it_n2).select_related('unite')

    liste_ru_stats = []
    maquette_totale = 0
    maint_global = None
    seen_unite_ids = set()

    # FIX PERF : suppression des manipulations de request.session["it"]
    # dans la boucle — SystEff/reelEff prennent 'it' en paramètre direct
    # et n'utilisent jamais la session ; ce code était inutile et coûteux.
    # 'tous_ru_it' est calculé une seule fois (ci-dessus) et transmis à
    # chaque appel au lieu d'être recalculé à chaque itération.
    for collab in collaborateurs_n1:
        ru_it = collab.it

        systeme = SystEff(collab.it, managers_its=tous_ru_it).values('it').distinct().count()
        reel = reelEff(collab.it, managers_its=tous_ru_it).values('it').distinct().count()

        last_decl = (
            declaration_effectif.objects
            .filter(Ru_id=ru_it, nature__in=["A", "V"])
            .order_by("-date")
            .first()
        )
        date_ref = last_decl.date if last_decl else None

        maquette_dedup = 0
        unit_key = getattr(collab, "unite_id", None)
        if unit_key and unit_key not in seen_unite_ids:
            maquette_dedup = getattr(collab.unite, "maquette", 0) or 0
            seen_unite_ids.add(unit_key)

        maquette_brute = getattr(collab.unite, "maquette", 0) or 0

        liste_ru_stats.append({
            "n1": collab,
            "matricule": getattr(collab, "matricule", None),
            "nom_complete": getattr(collab, "nom_complete", ""),
            "unite": getattr(collab, 'unite_id', None),
            "reel": reel,
            "systeme": systeme,
            "maquette": maquette_brute,
            "maquette_dedup": maquette_dedup,
            "mr": reel - maquette_brute,
            "ms": systeme - maquette_brute,
            "last_decl_date": date_ref,
        })

        maquette_totale += maquette_dedup
        if date_ref and (maint_global is None or date_ref > maint_global):
            maint_global = date_ref

    if maint_global is None:
        maint_global = timezone.localdate()

    tous_les_ru_ids = list(set(it_n1))
    liste_declares_today = set(
        declaration_effectif.objects
        .filter(date=today, Ru_id__in=tous_les_ru_ids)
        .values_list('Ru_id', flat=True)
    )
    non_valides = sum(
        1 for stat in liste_ru_stats
        if stat["systeme"] > 0 and stat["n1"].it not in liste_declares_today
    )

    total_r = (
        sum(stat["reel"] for stat in liste_ru_stats)
        + len(operateurs_directs_n3)
        + total_operateurs_directs_n2
        + nbr_n1_total
        + nbr_n2_total
    )
    total_syst = (
        sum(stat["systeme"] for stat in liste_ru_stats)
        + len(operateurs_directs_n3)
        + total_operateurs_directs_n2
        + nbr_n1_total
        + nbr_n2_total
    )

    operateurs_systeme_session = SystEff(it_session_original, managers_its=tous_ru_it)
    syste_session = operateurs_systeme_session.values('it').distinct().count()

    decl_window_qs = (
        declaration_effectif.objects
        .filter(Ru_id__in=it_n1, nature__in=["A", "V"], date__gte=start, date__lte=today)
        .values('Ru_id', 'date')
        .annotate(total=Count('collaborateur_it_id', distinct=True))
    )
    decls_by_ru = {}
    for r in decl_window_qs:
        decls_by_ru.setdefault(r['Ru_id'], []).append((r['date'], r['total']))
    for ru_key in decls_by_ru:
        decls_by_ru[ru_key].sort()

    part_fixe_par_jour = (
        len(operateurs_directs_n3)
        + total_operateurs_directs_n2
        + nbr_n1_total
        + nbr_n2_total
    )

    data_totale_par_jour = [part_fixe_par_jour] * len(dates)
    stat_dict = {stat["n1"].it: stat["systeme"] for stat in liste_ru_stats}

    for ru_it_key in [c.it for c in collaborateurs_n1]:
        ru_decls = decls_by_ru.get(ru_it_key, [])
        ru_system = stat_dict.get(ru_it_key, 0)
        ru_dates = [dt for dt, _ in ru_decls]
        ru_totals = [t for _, t in ru_decls]
        for idx, d in enumerate(dates):
            if ru_dates:
                pos = bisect.bisect_right(ru_dates, d) - 1
                if pos >= 0:
                    data_totale_par_jour[idx] += ru_totals[pos]
                    continue
            data_totale_par_jour[idx] += ru_system

    stats_by_it = {stat["n1"].it: stat for stat in liste_ru_stats}
    n1_directs_stats = [stats_by_it[c.it] for c in n1_directs if c.it in stats_by_it]

    n2_groups = []
    for n2 in n2_list:
        sous_managers = n2_to_n1.get(n2.it, [])
        sous_operateurs = n2_to_operateurs.get(n2.it, [])
        n1_stats_n2 = [stats_by_it[c.it] for c in sous_managers if c.it in stats_by_it]
        n2_groups.append({
            "n2": n2,
            "n1_stats": n1_stats_n2,
            "directs": {"liste": sous_operateurs, "reel": len(sous_operateurs)},
            "nbr_collabs_total": sum(s["reel"] for s in n1_stats_n2) + len(sous_operateurs),
        })

    lot_stats = {}

    def add_to_lot(lot_value, reel=0, systeme=0, maquette=0):
        lot_key = lot_value or "Non défini"
        if lot_key not in lot_stats:
            lot_stats[lot_key] = {"reel": 0, "systeme": 0, "maquette": 0}
        lot_stats[lot_key]["reel"] += reel
        lot_stats[lot_key]["systeme"] += systeme
        lot_stats[lot_key]["maquette"] += maquette

    for stat in liste_ru_stats:
        add_to_lot(
            getattr(stat["n1"], "lot", None),
            reel=stat["reel"],
            systeme=stat["systeme"],
            maquette=stat["maquette_dedup"],
        )

    for op in operateurs_directs_n3:
        add_to_lot(getattr(op, "lot", None), reel=1, systeme=1, maquette=0)

    for sous_operateurs in n2_to_operateurs.values():
        for op in sous_operateurs:
            add_to_lot(getattr(op, "lot", None), reel=1, systeme=1, maquette=0)

    lot_labels = list(lot_stats.keys())
    lot_reel_data = [lot_stats[l]["reel"] for l in lot_labels]
    lot_systeme_data = [lot_stats[l]["systeme"] for l in lot_labels]
    lot_maquette_data = [lot_stats[l]["maquette"] for l in lot_labels]

    lot_details = {}

    def add_detail(lot_value, nom, reel=0, systeme=0, maquette=0):
        lot_key = lot_value or "Non défini"
        lot_details.setdefault(lot_key, []).append({
            "nom": nom, "reel": reel, "systeme": systeme, "maquette": maquette
        })

    for stat in liste_ru_stats:
        add_detail(
            getattr(stat["n1"], "lot", None),
            getattr(stat["n1"], "nom_complete", stat["n1"].it),
            reel=stat["reel"], systeme=stat["systeme"], maquette=stat["maquette_dedup"],
        )
    for op in operateurs_directs_n3:
        add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)
    for sous_operateurs in n2_to_operateurs.values():
        for op in sous_operateurs:
            add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    labels_ru_liste = [s["n1"].nom_complete for s in liste_ru_stats]
    data_reel_liste = [s["reel"] for s in liste_ru_stats]
    data_systeme_liste = [s["systeme"] for s in liste_ru_stats]
    data_maquette_liste = [s["maquette"] for s in liste_ru_stats]

    context = {
        "liste_ru_stats": liste_ru_stats,
        "total_r": total_r,
        "total_syst": total_syst,
        "MR": total_r - maquette_totale,
        "MS": total_syst - maquette_totale,
        "maquette_totale": maquette_totale,
        "maint": maint_global,
        "non_valides": non_valides,
        "operateurs_systeme_session": operateurs_systeme_session,
        "syste_session": syste_session,
        "directs_n3": {"liste": operateurs_directs_n3, "reel": len(operateurs_directs_n3)},
        "operateurs_directs_n3": len(operateurs_directs_n3),
        "n1_directs": n1_directs,
        "n1_directs_stats": n1_directs_stats,
        "n2_list": n2_list,
        "n2_groups": n2_groups,
        "nbr_n1_total": nbr_n1_total,
        "nbr_n2_total": nbr_n2_total,
        "chart_labels": labels_list,
        "chart_data": data_totale_par_jour,
        "labels_ru": labels_ru_liste,
        "data_reel": data_reel_liste,
        "data_systeme": data_systeme_liste,
        "data_maquette": data_maquette_liste,
        "lot_labels": lot_labels,
        "lot_reel": lot_reel_data,
        "lot_systeme": lot_systeme_data,
        "lot_maquette": lot_maquette_data,
        "lot_details": lot_details,
    }
    return render(request, "declaration_effectif/N3/dashboard.html", context)


def _niveau_manager(it, tous_ru_it, cache):
    if it in cache:
        return cache[it]
    enfants_managers = list(
        Collaborateur.objects.filter(ru_it_id=it, it__in=tous_ru_it)
    )
    if not enfants_managers:
        niveau = 1
    else:
        niveau = 1 + max(_niveau_manager(c.it, tous_ru_it, cache) for c in enfants_managers)
    cache[it] = niveau
    return niveau


@role_required('N+4')
def page_N4(request):
    it_session_original = request.session.get("it")
    if not it_session_original:
        return redirect("login")

    tous_les_it = get_tous_les_it_sous(it_session_original)

    # FIX PERF : via le cache, au lieu d'une requête complète ici.
    tous_ru_it = get_managers_its()

    managers_ids = tous_les_it & tous_ru_it
    operateurs_ids = tous_les_it - tous_ru_it

    cache_niveau = {}
    niveau_par_it = {m: _niveau_manager(m, tous_ru_it, cache_niveau) for m in managers_ids}
    vrais_n1_ids = {m for m, n in niveau_par_it.items() if n == 1}

    directs_n4 = list(
        Collaborateur.objects.filter(ru_it_id=it_session_original)
        .exclude(it=it_session_original)
        .select_related('unite', 'departement')
    )
    operateurs_directs_n4 = [c for c in directs_n4 if c.it not in tous_ru_it]
    n1_directs_n4_collabs = [c for c in directs_n4 if niveau_par_it.get(c.it) == 1]
    n2_directs_n4 = [c for c in directs_n4 if niveau_par_it.get(c.it) == 2]
    n3_directs_n4 = [c for c in directs_n4 if niveau_par_it.get(c.it, 0) >= 3]

    if not vrais_n1_ids and not operateurs_ids:
        return render(request, "declaration_effectif/N4/dashboard.html", {
            "liste_ru_stats": [], "reel": 0, "systeme": 0, "maquette": 0,
            "MR": 0, "MS": 0, "maint": timezone.localdate(), "non_valides": 0,
            "chart_labels_json": json.dumps([]), "chart_data_json": json.dumps([]),
            "operateurs_directs_n4": {"liste": [], "reel": 0},
            "n1_directs_n4": [], "n2_groups": [], "n3_groups": [],
            "nbr_n1_total": 0, "nbr_n2_total": 0, "nbr_n3_total": 0,
            "lot_labels": [], "lot_reel": [], "lot_systeme": [], "lot_maquette": [],
            "lot_details": {},
        })

    today = timezone.now().date()
    start = today - timedelta(days=6)
    dates = [start + timedelta(days=i) for i in range(7)]
    labels_list = [d.strftime('%d %b') for d in dates]

    liste_n1 = Collaborateur.objects.filter(it__in=vrais_n1_ids).select_related('unite', 'departement')
    stats_by_it = {}
    maquette_dedup_par_n1 = {}
    maquette_totale = 0
    seen_unite_ids = set()
    maint_global = None

    # FIX PERF : plus de manipulation request.session["it"] dans la
    # boucle (inutile — voir dashboard_N3), et tous_ru_it/managers_its
    # transmis directement plutôt que recalculés à chaque itération.
    for collab in liste_n1:
        systeme = SystEff(collab.it, managers_its=tous_ru_it).values('it').distinct().count()
        reel = reelEff(collab.it, managers_its=tous_ru_it).values('it').distinct().count()

        last_decl = (
            declaration_effectif.objects
            .filter(Ru_id=collab.it, nature__in=["A", "V"])
            .order_by("-date").first()
        )
        date_ref = last_decl.date if last_decl else None

        maquette_dedup = 0
        unit_key = getattr(collab, "unite_id", None)
        if unit_key and unit_key not in seen_unite_ids:
            maquette_dedup = getattr(collab.unite, "maquette", 0) or 0
            seen_unite_ids.add(unit_key)
        maquette_dedup_par_n1[collab.it] = maquette_dedup
        maquette_brute = getattr(collab.unite, "maquette", 0) or 0

        stats_by_it[collab.it] = {
            "n1": collab,
            "matricule": getattr(collab, "matricule", None),
            "nom_complete": getattr(collab, "nom_complete", ""),
            "unite": getattr(collab, 'unite_id', None),
            "reel1": reel,
            "systeme1": systeme,
            "maquette1": maquette_brute,
            "mr": reel - maquette_brute,
            "ms": systeme - maquette_brute,
            "last_decl_date": date_ref,
        }
        maquette_totale += maquette_dedup
        if date_ref and (maint_global is None or date_ref > maint_global):
            maint_global = date_ref

    if maint_global is None:
        maint_global = timezone.localdate()

    liste_ru_stats = list(stats_by_it.values())

    operateurs_sous_vrais_n1 = set(
        Collaborateur.objects.filter(ru_it_id__in=vrais_n1_ids)
        .values_list("it", flat=True)
    )
    operateurs_intermediaires_ids = operateurs_ids - operateurs_sous_vrais_n1

    reel = sum(s["reel1"] for s in liste_ru_stats) + len(operateurs_intermediaires_ids) + len(managers_ids)
    systeme = sum(s["systeme1"] for s in liste_ru_stats) + len(operateurs_intermediaires_ids) + len(managers_ids)
    maquette = maquette_totale

    liste_declares_today = set(
        declaration_effectif.objects.filter(date=today, Ru_id__in=vrais_n1_ids)
        .values_list('Ru_id', flat=True)
    )
    non_valides = sum(
        1 for s in liste_ru_stats
        if s["systeme1"] > 0 and s["n1"].it not in liste_declares_today
    )

    decl_window_qs = (
        declaration_effectif.objects
        .filter(Ru_id__in=vrais_n1_ids, nature__in=["A", "V"], date__gte=start, date__lte=today)
        .values('Ru_id', 'date').annotate(total=Count('collaborateur_it_id', distinct=True))
    )
    decls_by_ru = {}
    for r in decl_window_qs:
        decls_by_ru.setdefault(r['Ru_id'], []).append((r['date'], r['total']))
    for k in decls_by_ru:
        decls_by_ru[k].sort()

    part_fixe_par_jour = len(operateurs_intermediaires_ids) + len(managers_ids)
    data_totale_par_jour = [part_fixe_par_jour] * len(dates)
    systeme_par_n1 = {s["n1"].it: s["systeme1"] for s in liste_ru_stats}

    for ru_it_key in vrais_n1_ids:
        ru_system = systeme_par_n1.get(ru_it_key, 0)
        ru_decls = decls_by_ru.get(ru_it_key, [])
        ru_dates = [dt for dt, _ in ru_decls]
        ru_totals = [t for _, t in ru_decls]
        for idx, d in enumerate(dates):
            if ru_dates:
                pos = bisect.bisect_right(ru_dates, d) - 1
                if pos >= 0:
                    data_totale_par_jour[idx] += ru_totals[pos]
                    continue
            data_totale_par_jour[idx] += ru_system

    def operateurs_directs_de(m_it):
        return list(
            Collaborateur.objects.filter(ru_it_id=m_it, it__in=operateurs_intermediaires_ids)
        )

    def sous_managers_de(m_it, niveau_cible):
        return [
            c for c in Collaborateur.objects.filter(ru_it_id=m_it, it__in=managers_ids)
            if niveau_par_it.get(c.it) == niveau_cible
        ]

    def construire_n2_group(n2):
        sous_n1 = sous_managers_de(n2.it, 1)
        n1_stats = [stats_by_it[c.it] for c in sous_n1 if c.it in stats_by_it]
        directs_ops = operateurs_directs_de(n2.it)
        return {
            "n2": n2,
            "n1_stats": n1_stats,
            "directs": {"liste": directs_ops, "reel": len(directs_ops)},
            "nbr_collabs_total": sum(s["reel1"] for s in n1_stats) + len(directs_ops),
        }

    def construire_n3_group(n3):
        sous_n2 = sous_managers_de(n3.it, 2)
        sous_n1_directs = sous_managers_de(n3.it, 1)
        n1_stats_directs = [stats_by_it[c.it] for c in sous_n1_directs if c.it in stats_by_it]
        n2_groups = [construire_n2_group(n2) for n2 in sous_n2]
        directs_ops = operateurs_directs_de(n3.it)
        nbr_total = (
            sum(g["nbr_collabs_total"] for g in n2_groups)
            + sum(s["reel1"] for s in n1_stats_directs)
            + len(directs_ops)
        )
        return {
            "n3": n3,
            "n1_stats_directs": n1_stats_directs,
            "n2_groups": n2_groups,
            "directs": {"liste": directs_ops, "reel": len(directs_ops)},
            "nbr_collabs_total": nbr_total,
        }

    n3_groups = [construire_n3_group(n3) for n3 in n3_directs_n4]
    n2_groups_directs_n4 = [construire_n2_group(n2) for n2 in n2_directs_n4]
    n1_stats_directs_n4 = [stats_by_it[c.it] for c in n1_directs_n4_collabs if c.it in stats_by_it]

    lot_stats = {}
    lot_details = {}

    def add_to_lot(lot_value, reel=0, systeme=0, maquette=0):
        lot_key = lot_value or "Non défini"
        if lot_key not in lot_stats:
            lot_stats[lot_key] = {"reel": 0, "systeme": 0, "maquette": 0}
        lot_stats[lot_key]["reel"] += reel
        lot_stats[lot_key]["systeme"] += systeme
        lot_stats[lot_key]["maquette"] += maquette

    def add_detail(lot_value, nom, reel=0, systeme=0, maquette=0):
        lot_key = lot_value or "Non défini"
        lot_details.setdefault(lot_key, []).append({
            "nom": nom, "reel": reel, "systeme": systeme, "maquette": maquette
        })

    for stat in liste_ru_stats:
        lot_val = getattr(stat["n1"], "lot", None)
        maq = maquette_dedup_par_n1.get(stat["n1"].it, 0)
        add_to_lot(lot_val, reel=stat["reel1"], systeme=stat["systeme1"], maquette=maq)
        add_detail(
            lot_val, getattr(stat["n1"], "nom_complete", stat["n1"].it),
            reel=stat["reel1"], systeme=stat["systeme1"], maquette=maq,
        )

    for op in operateurs_directs_n4:
        add_to_lot(getattr(op, "lot", None), reel=1, systeme=1, maquette=0)
        add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    ids_deja_comptes = {o.it for o in operateurs_directs_n4}
    operateurs_intermediaires_hors_n4 = Collaborateur.objects.filter(
        it__in=operateurs_intermediaires_ids
    ).exclude(it__in=ids_deja_comptes)

    for op in operateurs_intermediaires_hors_n4:
        add_to_lot(getattr(op, "lot", None), reel=1, systeme=1, maquette=0)
        add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    lot_labels = list(lot_stats.keys())
    lot_reel_data = [lot_stats[l]["reel"] for l in lot_labels]
    lot_systeme_data = [lot_stats[l]["systeme"] for l in lot_labels]
    lot_maquette_data = [lot_stats[l]["maquette"] for l in lot_labels]

    return render(request, "declaration_effectif/N4/dashboard.html", {
        "liste_ru_stats": liste_ru_stats,
        "reel": reel,
        "systeme": systeme,
        "maquette": maquette,
        "MR": reel - maquette,
        "MS": systeme - maquette,
        "maint": maint_global,
        "non_valides": non_valides,
        "chart_labels_json": json.dumps(labels_list),
        "chart_data_json": json.dumps(data_totale_par_jour),
        "operateurs_directs_n4": {"liste": operateurs_directs_n4, "reel": len(operateurs_directs_n4)},
        "n1_directs_n4": n1_stats_directs_n4,
        "n2_groups": n2_groups_directs_n4,
        "n3_groups": n3_groups,
        "nbr_n1_total": len(vrais_n1_ids),
        "nbr_n2_total": sum(1 for n in niveau_par_it.values() if n == 2),
        "nbr_n3_total": sum(1 for n in niveau_par_it.values() if n >= 3),
        "lot_labels": lot_labels,
        "lot_reel": lot_reel_data,
        "lot_systeme": lot_systeme_data,
        "lot_maquette": lot_maquette_data,
        "lot_details": lot_details,
    })

# ============================================================
# # rederiger LISTE DES AFFECTATION DE N+4
# ============================================================
@role_required('N+4')
def affectation_N4(request):
    util = request.session.get("it")
    if not util:
        return redirect("login")

    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "").strip()
    ru_acceuil = request.GET.get("ru_acceuil", "").strip()
    tab_actif = request.GET.get("tab", "tab-mes")

    liste_N3 = liste_N3_N4(util)

    tous_sous_n4 = list(liste_N3)
    for n3 in liste_N3:
        tous_sous_n4.extend(Rg_Dur(n3))
    tous_sous_n4.extend(liste_N1_pr_N3(util))

    # FIX PERF : une requête groupée par ensemble au lieu d'une par manager.
    toutes_declarations_N4 = list(historique_pour(liste_N3))
    toutes_declarations = list(historique_pour(tous_sous_n4))

    ensemble_onglet = toutes_declarations if tab_actif == "tab-toutes" else toutes_declarations_N4
    ru_initiaux = sorted({d.initial for d in ensemble_onglet if d.initial})
    ru_acceuils = sorted({d.acceuil for d in ensemble_onglet if d.acceuil})

    def filtrer(liste, mot_cle):
        return [d for d in liste if d.etat and mot_cle in str(d.etat).lower()]

    if status == "valide":
        toutes_declarations_N4 = filtrer(toutes_declarations_N4, "valid")
        toutes_declarations = filtrer(toutes_declarations, "valid")
    elif status == "refuse":
        toutes_declarations_N4 = filtrer(toutes_declarations_N4, "refus")
        toutes_declarations = filtrer(toutes_declarations, "refus")
    elif status == "non_demarrer":
        toutes_declarations_N4 = filtrer(toutes_declarations_N4, "non démarr")
        toutes_declarations = filtrer(toutes_declarations, "non démarr")

    if ru_init:
        toutes_declarations_N4 = [d for d in toutes_declarations_N4 if d.initial == ru_init]
        toutes_declarations = [d for d in toutes_declarations if d.initial == ru_init]

    if ru_acceuil:
        toutes_declarations_N4 = [d for d in toutes_declarations_N4 if d.acceuil == ru_acceuil]
        toutes_declarations = [d for d in toutes_declarations if d.acceuil == ru_acceuil]

    total_N4 = len(toutes_declarations_N4)
    total_nbr = len(toutes_declarations)

    paginator_n4 = Paginator(toutes_declarations_N4, 10)
    page_n4 = request.GET.get("page_n4", 1)
    page_obj_n4 = paginator_n4.get_page(page_n4)

    paginator_n1 = Paginator(toutes_declarations, 10)
    page_n1 = request.GET.get("page_n1", 1)
    page_obj_n1 = paginator_n1.get_page(page_n1)

    return render(
        request,
        "declaration_effectif/N2/affectation.html",
        {
            "n2": page_obj_n4,
            "page_obj_n2": page_obj_n4,
            "info": page_obj_n1,
            "page_obj_n1": page_obj_n1,
            "nbr2": total_N4,
            "nbr": total_nbr,
            "status": status,
            "ru_init": ru_init,
            "ru_acceuil": ru_acceuil,
            "ru_initiaux": ru_initiaux,
            "ru_acceuils": ru_acceuils,
        }
    )
# ============================================================
# # rederiger vers la page des N+1 sous N+4 non pas valider liste
# ============================================================
@role_required('N+4')
def validation_N4(request):
    util = request.session.get("it")

    if not util:
        return redirect("login")

    maint = timezone.localdate()

    n1_its = get_tous_les_n1(util)

    liste_declares = set(
        declaration_effectif.objects.filter(
            date=maint,
            Ru_id__in=n1_its
        ).values_list("Ru_id", flat=True)
    )

    non_valides = Collaborateur.objects.filter(
        it__in=n1_its
    ).exclude(
        it__in=liste_declares
    ).select_related("departement", "unite").order_by("nom_complete")

    total_non_valides = non_valides.count()

    paginator = Paginator(non_valides, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "declaration_effectif/N4/validation.html",
        {
            "non_valides": page_obj,
            "page_obj": page_obj,
            "total_non_valides": total_non_valides,
        },
    )

# ============================================================
# liste des validation filtre par date pr N+2
# ============================================================
def validation_date_N2(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    n1 = Ru_Rg(it)
    liste_n1 = set(n1.values_list("it", flat=True))

    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(
                date=query_date,
                Ru_id__in=liste_n1
            ).values_list("Ru_id", flat=True)
        )

    liste_it_manquants = liste_n1 - declarations_faites

    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )

    return JsonResponse({
        "resultats": resultats,
        "status": status
    })
# ============================================================
# liste des validation filtre par date pr N+3
# ============================================================
def validation_date_N3(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    liste_n1 = get_tous_les_n1(it)

    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(
                date=query_date,
                Ru_id__in=liste_n1
            ).values_list("Ru_id", flat=True)
        )

    liste_it_manquants = liste_n1 - declarations_faites

    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )

    return JsonResponse({
        "resultats": resultats,
        "status": status
    })


def get_badge_class(etat):
    if not etat:
        return "badge-en-attente"
    etat_lower = str(etat).lower()
    if "valid" in etat_lower:
        return "badge-valide"
    elif "refus" in etat_lower:
        return "badge-refuse"
    elif "non démarr" in etat_lower or "non demarr" in etat_lower:
        return "badge-non-demarrer"
    else:
        return "badge-en-attente"

@role_required(['HRBP', 'ADMIN'])
def affectation_HRBP(request):
    it = request.session.get("it")
    status = request.GET.get("status", "all")
    dpt_filtre = request.GET.get("dpt", "all")
    role=request.session.get("role")
    if role == "HRBP" :
        departements_qs = Departement.objects.filter(HRBP_id=it)
    elif role == "ADMIN":
        departements_qs = Departement.objects.filter(ADMIN_id=it)
    departements = list(departements_qs.values_list("abreviation", flat=True))

    affectation = historique.objects.filter(
        Q(dpt_init__in=departements) | Q(dpt_acceuil__in=departements)
    ).exclude(etat="Terminé")

    if status == "valide":
        affectation = affectation.filter(etat__icontains="valid")
    elif status == "refuse":
        affectation = affectation.filter(etat__icontains="refus")
    elif status == "non_demarrer":
        affectation = affectation.filter(etat__icontains="non démarr")

    if dpt_filtre != "all" and dpt_filtre in departements:
        affectation = affectation.filter(
            Q(dpt_init__abreviation=dpt_filtre) | Q(dpt_acceuil__abreviation=dpt_filtre)
        )
    paginator = Paginator(affectation, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    for a in page_obj:
        a.badge_class = get_badge_class(a.etat)

    return render(request, "declaration_effectif/HRBP/affectation.html", {
        "info": page_obj,
        "page_obj": page_obj,
        "status": status,
        "dpt_filtre": dpt_filtre,
        "departements": departements,
    })


@never_cache
@role_required('HRBP')
def responsables_ru_sans_declaration_du_jour(request):
    today = timezone.localdate()
    it = request.session.get("it")

    departements_qs = Departement.objects.filter(HRBP_id=it)
    departements = departements_qs.values_list("abreviation", flat=True)
    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)
    responsable = list(
        collaborateurs_base
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    operateur = Collaborateur.objects.filter(
        departement_id__in=departements
    ).exclude(it__in=responsable)

    ru_ids = list(
        operateur
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    ru_avec_declaration = set(
        declaration_effectif.objects.filter(date=today)
        .values_list("Ru_id", flat=True)
        .distinct()
    )

    ru_ids_sans_declaration = [
        ru_id for ru_id in ru_ids if ru_id not in ru_avec_declaration
    ]

    ru = Collaborateur.objects.filter(it__in=ru_ids_sans_declaration)

    paginator = Paginator(ru, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "declaration_effectif/HRBP/declaration.html", {
        "ru": page_obj,
        "non_valides": ru,
        "page_obj": page_obj,
        "departements": departements_qs,
        "date": today,
    })


def filter_date2(request):
    it = request.session.get("it")
    date_str = request.GET.get("time")
    dept = request.GET.get("dept", "").strip()

    if not date_str:
        return JsonResponse({"resultats": [], "status": False})

    try:
        date_selectionnee = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"resultats": [], "status": False})

    is_today = (date_selectionnee == timezone.localdate()) if date_selectionnee else False
    status = not is_today

    departements_qs = Departement.objects.filter(HRBP_id=it)
    departements = list(departements_qs.values_list("abreviation", flat=True))

    if dept:
        departements = [d for d in departements if d == dept]

    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)

    responsable = list(
        collaborateurs_base
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    operateur = Collaborateur.objects.filter(
        departement_id__in=departements
    ).exclude(it__in=responsable)

    ru_ids = list(
        operateur
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    ru_avec_declaration = set(
        declaration_effectif.objects.filter(date=date_selectionnee)
        .values_list("Ru_id", flat=True)
        .distinct()
    )

    ru_ids_sans_declaration = [
        ru_id for ru_id in ru_ids if ru_id not in ru_avec_declaration
    ]

    ru_qs = Collaborateur.objects.filter(
        it__in=ru_ids_sans_declaration
    ).select_related("departement")

    resultats = [
        {
            "matricule": r.matricule,
            "it": r.it,
            "nom_complete": r.nom_complete,
            "lot": r.lot,
            "departement": {
                "abreviation": r.departement.abreviation if r.departement else ""
            },
        }
        for r in ru_qs
    ]

    return JsonResponse({
        "resultats": resultats,
        "status": status
    })

# ============================================================
# liste des validation filtre par date pr N+4
# ============================================================
def validation_date_N4(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    # FIX : utilise get_tous_les_n1 (cohérent avec validation_N4 et
    # validation_date_N3), plutôt que liste_N3_N4 + liste_N1_pr_N3
    # qui ratait les N+1 imbriqués plus profondément.
    liste_n1 = get_tous_les_n1(it)

    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(
                date=query_date,
                Ru_id__in=liste_n1
            ).values_list("Ru_id", flat=True)
        )

    liste_it_manquants = liste_n1 - declarations_faites

    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )

    return JsonResponse({
        "resultats": resultats,
        "status": status
    })

def changement_dpt(request):
    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)

    status = request.GET.get("status", "all")
    page_number = request.GET.get("page", 1)

    changement = (
        historique.objects
        .filter(Q(dpt_init=departement) | Q(dpt_acceuil=departement))
        .exclude(etat="Terminé")
        .order_by("-id")
    )

    if status == "valide":
        changement = changement.filter(etat__icontains="valid")
    elif status == "refuse":
        changement = changement.filter(etat__icontains="refus")
    elif status == "non_demarrer":
        changement = changement.filter(etat__icontains="non démarr")

    nbr = changement.count()

    paginator = Paginator(changement, 20)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)

    return render(
        request,
        "declaration_effectif/PILOT/affectation.html",
        {
            "info": page_obj,
            "page_obj": page_obj,
            "nbr": nbr,
            "status": status,
        },
    )