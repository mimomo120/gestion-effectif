from django.shortcuts import render, redirect
from utilisateur.models import utilisateur
from Collaborateur.models import  Departement , Collaborateur ,Unite
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q ,Count ,Max ,F
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif ,Alert ,historique
from django.http import JsonResponse
from datetime import date
from declaration_effectif.views import difference , histo_aff
from Collaborateur.views import rec ,Ru_Rg,liste_N1_pr_N3,Rg_Dur ,reelEff ,SystEff
from django.db.models import Sum
from .decorators import role_required
from datetime import timedelta
import json
import bisect
from .forms import RegisterForm
from django.db import transaction, IntegrityError
from collections import Counter
from django.http import HttpResponseForbidden
import secrets
from collections import defaultdict
import string
from django.views.decorators.http import require_POST
# ============================================================
# login_view : authentifie l'utilisateur et l'aiguille vers le
# tableau de bord correspondant à son rôle actif (N+1 à N+4).
# ============================================================
def login_view(request):
    if request.method == "POST":
        it = request.POST.get("it")
        password = request.POST.get("password")
        try:
            utilis = utilisateur.objects.get(pk=it)
            collab = utilis.it
            if check_password(password, utilis.password):
                request.session["it"] = utilis.it.it
                request.session["role"] = utilis.role
                request.session["nom"] = collab.nom_complete
                roles_disponibles = []
    
                if utilis.N1 :
                    roles_disponibles.append("N+1")
                if utilis.N2 :
                            roles_disponibles.append("N+2")
                if utilis.N3 :
                            roles_disponibles.append("N+3")
                if utilis.N4 :
                            roles_disponibles.append("N+4")
                if utilis.SUPER :
                    roles_disponibles.append("SUPER")
                if utilis.ADMIN :
                    roles_disponibles.append("ADMIN")
                if utilis.HRBP :
                    roles_disponibles.append("HRBP")
                if utilis.DRH :
                    roles_disponibles.append("DRH")

                request.session['roles_disponibles'] = roles_disponibles
                if utilis.role == "N+1":
                    return redirect('dashboard_N1')

                elif utilis.role == "N+2":
                    return redirect("dashboard_N2")
                elif utilis.role == "N+3":
                    return redirect("Dashboard_N3")
                    
                elif utilis.role == "N+4":
                    return redirect('page_N4')
                
                elif utilis.role == "SUPER":
                    return redirect('SUPER')
                elif utilis.role == "HRBP":
                    return redirect('dashboard')
                elif utilis.role == "DRH":
                    return redirect('dashboard')
                elif utilis.role == "ADMIN":
                    return redirect('dashboard')
            else:
                return render(
                    request,
                    "utilisateur/login.html",
                    {"message": "Mot de passe incorrect"}
                )
        except utilisateur.DoesNotExist:
            return render(
                request,
                "utilisateur/login.html",
                {"message": "Identifiant introuvable"}
            )
    else:
        return render(
                request,
                "utilisateur/login.html")


# ============================================================
# register_view : crée un compte utilisateur pour un IT donné en
# déduisant automatiquement son rôle (N+1 à N+4) à partir de sa
# position dans la hiérarchie des Collaborateur (ru_it).
# ============================================================

def register_view(request):
    if request.method == "POST":
        it = request.POST.get('it')
        password = request.POST.get('password')

        # 1. Validation des champs obligatoires
        if not it or not password:
            return render(request, "utilisateur/register.html", {
                'message': "Vous devez remplir tous les champs."
            })

        # 2. Vérification de l'existence dans le modèle Collaborateur
        try:
            col = Collaborateur.objects.get(it=it)
        except Collaborateur.DoesNotExist:
            return render(request, "utilisateur/register.html", {
                'message': "Ce collaborateur n'existe pas dans la base de données."
            })

        # 3. Vérification si l'utilisateur est déjà inscrit
        if utilisateur.objects.filter(it=col).exists():
            return render(request, "utilisateur/register.html", {
                'message': "Cet utilisateur est déjà enregistré."
            })

        # 4. Calcul de la hiérarchie (utilisation correcte de 'it')
        role_calcule, n1, n2, n3, n4 = determiner_hierarchie(it)

        if not role_calcule:
            return render(request, "utilisateur/accesInterdi.html")

        # 5. Création ou mise à jour avec l'instance 'col'
        util, created = utilisateur.objects.update_or_create(
    it=col,
    defaults={
        "role": role_calcule,
        "password": make_password(password),
        "N1": n1,
        "N2": n2,
        "N3": n3,
        "N4": n4,
        "ADMIN": 0,
        "HRBP": 0,
        "SUPER": 0,"DRH":0
    }
)

        return render(request, "utilisateur/login.html")

    return render(request, "utilisateur/register.html")

def determiner_hierarchie(it_val):
    """Calcule le rôle et les flags N1..N4 pour un identifiant IT."""
    managers_ids = set(
        Collaborateur.objects.exclude(ru_it_id__isnull=True)
        .exclude(ru_it_id=F('it'))
        .values_list("ru_it_id", flat=True)
    )
    operateurs = Collaborateur.objects.exclude(it__in=managers_ids)

    # Niveau N1
    liste_N1 = set(operateurs.values_list("ru_it_id", flat=True))
    liste1 = Collaborateur.objects.filter(it__in=liste_N1)
    l1 = set(liste1.values_list("it", flat=True))

    # Niveau N2
    liste_N2 = set(liste1.values_list("ru_it_id", flat=True))
    liste_N2.discard(None)
    liste2 = Collaborateur.objects.filter(it__in=liste_N2)
    l2 = set(liste2.values_list("it", flat=True))

    # Niveau N3
    liste_N3 = set(liste2.values_list("ru_it_id", flat=True))
    liste_N3.discard(None)
    liste3 = Collaborateur.objects.filter(it__in=liste_N3)
    l3 = set(liste3.values_list("it", flat=True))

    # Niveau N4
    liste_N4 = set(liste3.values_list("ru_it_id", flat=True))
    liste_N4.discard(None)
    liste4 = Collaborateur.objects.filter(it__in=liste_N4)
    l4 = set(liste4.values_list("it", flat=True))
    n1_flag = 1 if it_val in l1 else 0
    n2_flag = 1 if it_val in l2 else 0
    n3_flag = 1 if it_val in l3 else 0
    n4_flag = 1 if it_val in l4 else 0

    role_calcule = None
    if it_val in l4:
        role_calcule = "N+4"
    elif it_val in l3:
        role_calcule = "N+3"
    elif it_val in l2:
        role_calcule = "N+2"
    elif it_val in l1:
        role_calcule = "N+1"

    return role_calcule, n1_flag, n2_flag, n3_flag, n4_flag

# ============================================================
# tableau : construit le tableau de bord d'un N+1 (RU) — effectif
# système vs réel vs maquette, répartition par lot, et données du
# graphique des 7 derniers jours de déclaration.
# ============================================================
@role_required('N+1')
def tableau(request):
    # 1. Récupération des informations du RU
    it = request.session.get("it")
    if not it:
        return HttpResponseForbidden("Session expirée, veuillez vous reconnecter.")

    try:
        ru = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return HttpResponseForbidden("Collaborateur introuvable.")

    # 2. Opérateurs Système & Réel
    operateurs_systeme = SystEff(it)
    syste = operateurs_systeme.values('it').distinct().count()

    operateurs_reel = reelEff(it)
    counts_qs = operateurs_reel.values('lot').annotate(total=Count('it', distinct=True))
    counts_par_lot = {r['lot']: r['total'] for r in counts_qs}
    counts_ls = operateurs_systeme.values('lot').annotate(total=Count('it', distinct=True))
    counts_lot_ls = {r['lot']: r['total'] for r in counts_ls}
    reel = operateurs_reel.values('it').distinct().count()

    # Différences (Système vs Réel)
    diff = difference(request)
    systeme = diff["systeme1"]
    reel1 = diff["reel1"]

    # 3. Récupération de la Maquette & Unité
    unite = None
    maquette = 0
    A = E = P = C = 0
    if ru.unite_id:
        try:
            unite = Unite.objects.get(abreviation=ru.unite_id)
            maquette = unite.maquette or 0
            A = unite.A or 0
            E = unite.T or 0
            P = unite.P or 0
            C = unite.C or 0
        except Unite.DoesNotExist:
            pass
        except Unite.MultipleObjectsReturned:
            unite = Unite.objects.filter(pk=ru.unite_id).first()
            if unite:
                maquette = unite.maquette or 0
                A = unite.A or 0
                E = unite.T or 0
                P = unite.P or 0
                C = unite.C or 0

    # 4. Calculs par Lots
    Pr = counts_par_lot.get("P", 0)
    Ar = counts_par_lot.get("A", 0)
    OLr = counts_par_lot.get("O", 0)
    Er = counts_par_lot.get("E", 0)
    Cr = counts_par_lot.get("C", 0)

    Ps = counts_lot_ls.get("P", 0)
    As = counts_lot_ls.get("A", 0)
    OLs = counts_lot_ls.get("O", 0)
    Es = counts_lot_ls.get("E", 0)
    Cs = counts_lot_ls.get("C", 0)

    # Écarts vs Maquette
    diff_r_m = reel - maquette
    diff_s_m =  syste - maquette

    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    date_declaration = der.date if der else timezone.localdate()
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime('%d %b') for d in dates]

    data_list = [
        reelEff(it, date_reference=d).values('it').distinct().count()
        for d in dates
    ]

    # 5. Derniers mouvements effectués par le RU
    derniers_mouvements = derniers_mouvements_respo(it, limite=10)

    # 7. Rendu Final
    context = {
        'chart_labels': json.dumps(labels_list),
        'chart_data': json.dumps(data_list),

        'operateurs_systeme': operateurs_systeme,
        'syste': syste,
        'reel': reel,
        'diff_r_m': diff_r_m,
        'diff_s_m': diff_s_m,
        'date': date_declaration,

        'reel1': reel1,
        'systeme1': systeme,

        'Pr': Pr, 'Ar': Ar + OLr, 'Er': Er, 'Cr': Cr,
        'A': A, 'C': C, 'P': P, 'E': E,
        'maquette': maquette,

        'reel_par_lot_json': json.dumps([Ar + OLr, Pr, Er, Cr]),
        'maquette_par_lot_json': json.dumps([A, P, E, C]),
        'systeme_par_lot_json': json.dumps([As + OLs, Ps, Es, Cs]),

        'derniers_mouvements': derniers_mouvements,
    }
    return render(request, "declaration_effectif/N1/dashboard_N1.html", context)


# ============================================================
# verifier : endpoint AJAX qui vérifie si un IT donné correspond
# bien à un utilisateur ayant le rôle N+1 (utilisé typiquement pour
# valider un champ de formulaire de saisie de RU).
# ============================================================
def verifier(request):
    it=request.GET.get("q","")
    if it:
        nbr=utilisateur.objects.filter(it_id=it,role="N+1").exists()
        return JsonResponse({"valide": nbr})
    
# ============================================================
# deconnecter : vide la session et renvoie vers la page de login.
# ============================================================
def deconnecter(request):
    request.session.flush()
    return redirect("login")

# ============================================================
# dashboard_rg : tableau de bord consolidé d'un N+2 (RG) — agrège
# les effectifs système/réel/maquette de tous les RU (N+1) sous sa
# responsabilité, et compte les RU n'ayant pas encore déclaré.
# ============================================================
@role_required("N+2")
def dashboard_N2(request):
    it = request.session.get("it")
    liste_ru = Ru_Rg(it)
    ru_its = set(liste_ru.values_list("it", flat=True))
    unite_abrs = set(liste_ru.values_list("unite_id", flat=True))
    maint = timezone.localdate()
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime('%d %b') for d in dates]

    # Somme des effectifs réels de tous les RU, pour chaque date
    data_list = []
    for d in dates:
        total_jour = 0
        for ru in ru_its:
            total_jour += reelEff(ru, date_reference=d).values('it').distinct().count()
        data_list.append(total_jour)

    # 1. Déclarations du jour
    liste_declares = set(
        declaration_effectif.objects.filter(date=maint, Ru_id__in=ru_its)
        .values_list("Ru_id", flat=True)
    )

    # 2. RU n'ayant pas encore effectué leur déclaration
    liste_ru_avec_operateurs = set(
        Collaborateur.objects.filter(ru_it_id__in=ru_its)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    non_valides = (
        Collaborateur.objects.filter(it__in=liste_ru_avec_operateurs)
        .exclude(it__in=liste_declares)
        .count()
    )

    # 3. Récupération des Unités (Maquette) sous forme de dictionnaire {abreviation: maquette}
    unites_map = {
        u.abreviation: (u.maquette or 0)
        for u in Unite.objects.filter(abreviation__in=unite_abrs)
    }
    maquette_total = sum(unites_map.values())

    # 4. Effectifs système par RU en 1 seule requête SQL
    systeme_counts = dict(
        Collaborateur.objects.filter(ru_it_id__in=ru_its)
        .values('ru_it_id')
        .annotate(total=Count('it'))
        .values_list('ru_it_id', 'total')
    )

    # 5. Dernières dates de déclaration par RU en 1 seule requête SQL
    dernieres_dates = dict(
        declaration_effectif.objects.filter(Ru_id__in=ru_its)
        .values('Ru_id')
        .annotate(max_date=Max('date'))
        .values_list('Ru_id', 'max_date')
    )

    # 6. Effectifs réels (nature 'A' ou 'V') correspondant aux dernières dates par RU
    q_conditions = Q()
    for ru_id, max_date in dernieres_dates.items():
        if max_date:
            q_conditions |= Q(Ru_id=ru_id, date=max_date)

    reels_counts = {}
    if q_conditions:
        reels_counts = dict(
            declaration_effectif.objects.filter(q_conditions, nature__in=["A", "V"])
            .values('Ru_id')
            .annotate(total=Count('id'))
            .values_list('Ru_id', 'total')
        )

    # 6bis. Derniers mouvements (Départ / Changement / Ajout) pour tous les RU en 1 seule requête
    NB_MOUVEMENTS_PAR_RU = 5

    toutes_declarations = (
        declaration_effectif.objects
        .filter(Ru_id__in=ru_its, nature__in=["D", "C", "A"])
        .select_related('collaborateur_it', 'nv_Ru')
        .order_by('Ru_id', '-date', '-id')
    )

    mouvements_par_ru = {}
    for d in toutes_declarations:
        ru_id = d.Ru_id
        if ru_id not in mouvements_par_ru:
            mouvements_par_ru[ru_id] = []
        if len(mouvements_par_ru[ru_id]) < NB_MOUVEMENTS_PAR_RU:
            mouvements_par_ru[ru_id].append({
                "collaborateur": d.collaborateur_it.nom_complete if d.collaborateur_it else None,
                "collaborateur_it": d.collaborateur_it.it if d.collaborateur_it else None,
                "nature": d.nature,
                "nature_display": d.get_nature_display(),
                "nouveau_responsable": d.nv_Ru.nom_complete if d.nv_Ru else None,
                "date": d.date.strftime("%d/%m/%Y"),
            })

    # 7. Construction de la liste des résultats sans requêtes SQL supplémentaires
    liste_ru_stats = []
    effectif_syste = 0
    effectif_reel = 0

    for a in liste_ru:
        systeme = SystEff(a.it).count()
        effectif_syste += systeme
        reel = reelEff(a.it).count()

        effectif_reel += reel

        maquette = unites_map.get(a.unite_id, 0)

        liste_ru_stats.append({
            "matricule": a.matricule,
            "it": a.it,
            "nom_complete": a.nom_complete,
            "lot": a.lot,
            "unite": a.unite_id,
            "dpt": a.departement_id,
            "reel": reel,
            "systeme": systeme,
            "maquette": maquette,
            "MS": systeme - maquette,
            "MR": reel - maquette,
            "derniers_mouvements": mouvements_par_ru.get(a.it, []),
        })

    # Calcul des écarts globaux
    MR = effectif_reel - maquette_total
    MS = effectif_syste - maquette_total

    context = {
        'chart_labels': json.dumps(labels_list),
        'chart_data': json.dumps(data_list),
        "effectif_reel": effectif_reel,
        "effectif_syste": effectif_syste,
        "maquette_total": maquette_total,
        "non_valides": non_valides,
        "liste_ru_stats": liste_ru_stats,
        "MR": MR,
        "MS": MS,
        "maint": maint,
    }

    return render(request, "declaration_effectif/N2/dashboard_N2.html", context)

# ============================================================
# alertes : endpoint AJAX appelé quand un utilisateur ouvre son
# panneau de notifications — marque toutes ses alertes non lues
# comme lues.
# ============================================================

def alerts(request):
    it = request.session.get("it")
    alertes = Alert.objects.filter(recepteur=it, lu=False)
    alertes.update(lu=True)
    return JsonResponse({"statu": "true"})

# ============================================================
# changer_role : bascule le rôle actif de l'utilisateur (parmi ceux
# qu'il a le droit d'endosser, stockés en session) et le renvoie à
# la page de login pour être redirigé vers son nouveau tableau de
# bord.
# ============================================================
def changer_role(request, nouveau_role):
    roles_dispo = request.session.get('roles_disponibles', [])
    it=request.session.get("it")
    user=utilisateur.objects.get(it=it)
    if nouveau_role in roles_dispo:
        request.session['role'] = nouveau_role
        user.role=nouveau_role
        user.save()
        messages.success(request, f"Rôle actif changé vers {nouveau_role}")
        return redirect('login')
    else:
        messages.error(request, "Vous n'avez pas ce rôle.")
    return redirect(request.META.get('HTTP_REFERER', 'ru'))


# ============================================================
# notifications : context processor global (déclaré dans
# TEMPLATES/OPTIONS/context_processors) — injecte automatiquement
# la liste des notifications et leur compteur non-lu dans TOUS les
# templates rendus, sans que chaque vue ait à le faire explicitement.
# ============================================================
def notifications(request):
    it = request.session.get("it")
    alert = Alert.objects.filter(recepteur=it)
    nv = alert.filter(lu=False).count()
    return {
        "notifications": alert.order_by("-date")[:5],
        "nb_notifications": nv,
    }
@role_required('SUPER')
def SUPER_dashboard(request):
        it=request.session.get("it")
        util = utilisateur.objects.all().exclude(it_id=it)
        total=util.count()
        departements=Departement.objects.all().distinct()
        return render(request,"declaration_effectif/Super/dashboard.html",{"utilisateurs":util,"total":total,"departements":departements})

def generer_mot_de_passe_temporaire(longueur=12):
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(secrets.choice(alphabet) for _ in range(longueur))

@role_required('SUPER')
def ajouter_user(request):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée."}, status=405)

    try:
        data = json.loads(request.body)
        it_val = data.get("it")
        role_form = data.get("role")  # Peut être vide désormais

        if not it_val:
            return JsonResponse({"error": "Champs manquants."}, status=400)

        try:
            col = Collaborateur.objects.get(it=it_val)
        except Collaborateur.DoesNotExist:
            return JsonResponse({"error": "Ce collaborateur n'existe pas."}, status=404)

        if utilisateur.objects.filter(it=col).exists():
            return JsonResponse({"error": "Cet utilisateur est déjà enregistré."}, status=400)

        role_calcule, n1, n2, n3, n4 = determiner_hierarchie(it_val)

        role_final = role_form or role_calcule

        if not role_final:
            return JsonResponse({"error": "Impossible de déterminer un rôle pour cet utilisateur."}, status=400)

        mdp_temporaire = generer_mot_de_passe_temporaire()

        util, created = utilisateur.objects.update_or_create(
            it=col,
            defaults={
                "role": role_final,
                "password": make_password(mdp_temporaire),
                "N1": n1,
                "N2": n2,
                "N3": n3,
                "N4": n4,
                "ADMIN": 1 if role_final == "ADMIN" else 0,
                "HRBP": 1 if role_final == "HRBP" else 0,
                "SUPER": 1 if role_final == "SUPER" else 0,"DRH": 1 if role_final == "DRH" else 0,
            }
        )

        return JsonResponse({
            "message": "Utilisateur enregistré avec succès !",
            "mdp_temporaire": mdp_temporaire,
            "created": created
        }, status=200)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
@role_required('SUPER')
def supprimer_user(request, id):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée."}, status=405)

    it_session = request.session.get("it")
    try:
        util = utilisateur.objects.get(pk=id)
    except utilisateur.DoesNotExist:
        return JsonResponse({"error": "Utilisateur introuvable."}, status=404)

    if util.it_id == it_session:
        return JsonResponse({"error": "Vous ne pouvez pas supprimer votre propre compte."}, status=400)

    util.delete()
    return JsonResponse({"message": "Utilisateur supprimé avec succès."}, status=200)
@role_required('SUPER')
def modifier_user(request, id):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée."}, status=405)

    try:
        util = utilisateur.objects.get(pk=id)
    except utilisateur.DoesNotExist:
        return JsonResponse({"error": "Utilisateur introuvable."}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Requête invalide."}, status=400)

    role_form = (data.get("role") or "").strip()

    role_calcule, n1, n2, n3, n4 = determiner_hierarchie(util.it_id)
    role_final = role_form or role_calcule

    if not role_final:
        return JsonResponse({"error": "Impossible de déterminer un rôle pour cet utilisateur."}, status=400)

    util.role = role_final
    util.ADMIN = 1 if role_final == "ADMIN" else 0
    util.HRBP = 1 if role_final == "HRBP" else 0
    util.SUPER = 1 if role_final == "SUPER" else 0
    util.save()

    return JsonResponse({
        "message": "Utilisateur mis à jour avec succès.",
        "role": role_final
    }, status=200)


def calculer_evolution_effectif_reel(departements, jours=30):
    collaborateurs = Collaborateur.objects.filter(departement_id__in=departements)
    liste_collab_ids = list(collaborateurs.values_list("it", flat=True))
    total_collab = len(liste_collab_ids)

    today = timezone.now().date()
    date_debut = today - timedelta(days=jours)

    declarations = declaration_effectif.objects.filter(
        collaborateur_it__in=liste_collab_ids,
        date__lte=today
    ).order_by("collaborateur_it_id", "date").values(
        "collaborateur_it_id", "date", "nature"
    )

    declarations_par_collab = defaultdict(list)
    for d in declarations:
        declarations_par_collab[d["collaborateur_it_id"]].append(
            (d["date"], d["nature"])
        )

    labels = []
    valeurs = []
    date_courante = date_debut
    while date_courante <= today:
        nb_depart = 0
        for collab_id, decls in declarations_par_collab.items():
            derniere_nature = None
            for date_d, nature in decls:
                if date_d <= date_courante:
                    derniere_nature = nature
                else:
                    break
            if derniere_nature == "D":
                nb_depart += 1

        labels.append(date_courante.strftime("%d/%m"))
        valeurs.append(total_collab - nb_depart)
        date_courante += timedelta(days=1)

    return {"labels": labels, "valeurs": valeurs}

@role_required(["HRBP", "DRH", "ADMIN"])
def dashboard_rh(request):
    it = request.session.get("it")
    role = request.session.get("role")

    if role == "HRBP":
        y_min = 3480
        y_max = 3510
        departements_qs = Departement.objects.filter(HRBP_id=it)
    elif role == "DRH":
        y_min = 6770
        y_max = 6800
        departements_qs = Departement.objects.filter(DRH_id=it)
    elif role == "ADMIN":
        y_min = 3500
        y_max = 3530
        departements_qs = Departement.objects.filter(ADMIN_id=it)
    else:
        y_min = 0
        y_max = 0
        departements_qs = Departement.objects.none()

    departements = list(departements_qs.values_list("abreviation", flat=True))

    colSyst = Collaborateur.objects.filter(departement_id__in=departements).count()

    declarations_activite = (
        declaration_effectif.objects
        .filter(collaborateur_it__departement_id__in=departements, nature__in=["D", "A"])
        .order_by("collaborateur_it_id", "-date", "-id")
    )

    dernier_etat_activite = {}
    for decl in declarations_activite:
        if decl.collaborateur_it_id not in dernier_etat_activite:
            dernier_etat_activite[decl.collaborateur_it_id] = decl.nature

    liste_D = {c for c, nature in dernier_etat_activite.items() if nature == "D"}

    colReel = Collaborateur.objects.filter(
        departement_id__in=departements
    ).exclude(it__in=liste_D).count()

    maquette = departements_qs.aggregate(total=Sum("maquette"))["total"] or 0

    syst_par_dept = dict(
        Collaborateur.objects.filter(departement_id__in=departements)
        .values("departement_id")
        .annotate(total=Count("it"))
        .values_list("departement_id", "total")
    )

    reel_par_dept = dict(
        Collaborateur.objects.filter(departement_id__in=departements)
        .exclude(it__in=liste_D)
        .values("departement_id")
        .annotate(total=Count("it"))
        .values_list("departement_id", "total")
    )

    depart_par_dept = dict(
        Collaborateur.objects.filter(departement_id__in=departements, it__in=liste_D)
        .values("departement_id")
        .annotate(total=Count("it"))
        .values_list("departement_id", "total")
    )

    depart_TOT = 0
    detail_par_dept = []
    for dept in departements_qs:
        abrev = dept.abreviation
        syst_dept = syst_par_dept.get(abrev, 0)
        reel_dept = reel_par_dept.get(abrev, 0)
        nb_depart_dept = depart_par_dept.get(abrev, 0)
        depart_TOT += nb_depart_dept
        detail_par_dept.append({
            "abreviation": abrev,
            "nom": getattr(dept, "nom", abrev),
            "syst": syst_dept,
            "reel": reel_dept,
            "nb_depart": nb_depart_dept,
            "maquette": dept.maquette or 0,
            "ms": (dept.maquette or 0) - syst_dept,
            "mr": (dept.maquette or 0) - reel_dept,
        })

    graphe = calculer_evolution_effectif_reel(departements)

    date_depart = request.GET.get("date_depart")
    date_changement = request.GET.get("date_changement")

    liste_departs_qs = declaration_effectif.objects.filter(
        collaborateur_it__departement_id__in=departements,
        nature="D",
        collaborateur_it_id__in=liste_D,
    )
    if date_depart:
        liste_departs_qs = liste_departs_qs.filter(date=date_depart)

    liste_departs = liste_departs_qs.select_related(
        "collaborateur_it", "collaborateur_it__departement", "Ru"
    ).order_by("-date")[:10]

    liste_changements_qs = declaration_effectif.objects.filter(
        collaborateur_it__departement_id__in=departements,
        nature="C"
    )
    if date_changement:
        liste_changements_qs = liste_changements_qs.filter(date=date_changement)

    liste_changements = liste_changements_qs.select_related(
        "collaborateur_it", "collaborateur_it__departement", "Ru", "nv_Ru"
    ).order_by("-date")[:10]

    today = timezone.now().date()
    
    context = {
        "departements": departements,
        "colSyst": colSyst,
        "colReel": colReel,
        "today": today,
        "maquette": maquette,
        "MS": maquette - colSyst,
        "MR": maquette - colReel,
        "labels": graphe["labels"],
        "valeurs": graphe["valeurs"],
        "detail_par_dept": detail_par_dept,
        "depart_TOT": depart_TOT,
        "liste_departs": liste_departs,
        "liste_changements": liste_changements,
        "y_min": y_min,
        "y_max": y_max,
        "date_depart": date_depart or "",
        "date_changement": date_changement or "",
    }

    return render(request, "declaration_effectif/HRBP/dashboard.html", context)


def derniers_mouvements_respo(it_respo, limite=10):
    """
    Fonction utilitaire : retourne une liste de dicts (pas de JsonResponse).
    """
    if not it_respo:
        return []

    declarations = (
        declaration_effectif.objects
        .filter(Ru__it=it_respo,nature__in=["A","D","C"])
        .select_related('collaborateur_it', 'Ru', 'nv_Ru')
        .order_by('-date', '-id')[:limite]
    )

    return [
        {
            "id": d.pk,
            "collaborateur": d.collaborateur_it.nom_complete if d.collaborateur_it else None,
            "collaborateur_it": d.collaborateur_it.it if d.collaborateur_it else None,
            "nature": d.nature,
            "nature_display": d.get_nature_display(),
            "nouveau_responsable": d.nv_Ru.nom_complete if d.nv_Ru else None,
            "nouveau_responsable_it": d.nv_Ru.it if d.nv_Ru else None,
            "date": d.date.strftime("%d/%m/%Y"),
        }
        for d in declarations
    ]


def changer_mot_de_passe(request):
    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "Session expirée, veuillez vous reconnecter."}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Requête invalide."}, status=400)

    ancien_password = data.get("ancien_password", "")
    nouveau_password = data.get("nouveau_password", "")

    if not ancien_password or not nouveau_password:
        return JsonResponse({"error": "Tous les champs sont requis."}, status=400)

    if len(nouveau_password) < 8:
        return JsonResponse({"error": "Le nouveau mot de passe doit contenir au moins 8 caractères."}, status=400)

    try:
        user = utilisateur.objects.get(it_id=it)
    except utilisateur.DoesNotExist:
        return JsonResponse({"error": "Utilisateur introuvable."}, status=404)

    # Vérification de l'ancien mot de passe via la méthode du modèle
    if not user.check_password(ancien_password):
        return JsonResponse({"error": "Mot de passe actuel incorrect."}, status=400)

    user.set_password(nouveau_password)

    return JsonResponse({"success": True})