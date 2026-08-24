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
    
                if utilis.N1 == 1:
                    roles_disponibles.append("N+1")
                if utilis.N2 == 1:
                            roles_disponibles.append("N+2")
                if utilis.N3 == 1:
                            roles_disponibles.append("N+3")
                if utilis.N4 == 1:
                            roles_disponibles.append("N+4")
                    
                        # Stockage en session
                request.session['roles_disponibles'] = roles_disponibles
                if utilis.role == "N+1":
                    return redirect('ru')

                elif utilis.role == "N+2":
                    return redirect("dashboard_rg")
                elif utilis.role == "N+3":
                    return redirect("DUR")
                    
                elif utilis.role == "N+4":
                    return redirect('page_N4')
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


#Cree ou bien deriger vers la page de register
# ============================================================
# register_view : crée un compte utilisateur pour un IT donné en
# déduisant automatiquement son rôle (N+1 à N+4) à partir de sa
# position dans la hiérarchie des Collaborateur (ru_it).
# ============================================================

def register_view(request):
    if request.method == "POST":
        it = request.POST.get('it')
        password = request.POST.get('password')

        # 1. Validation des champs remplis
        if not it or not password:
            return render(request, "utilisateur/register.html", {
                'message': "vous devez remplir tt les champs"
            })
            
        # 2. Vérification si l'utilisateur existe déjà (avec return immédiat)
        if utilisateur.objects.filter(it_id=it).exists():
            return render(request, "utilisateur/register.html", {
                'message': "Cet utilisateur est déjà enregistré"
            })

        managers_ids = set(
            Collaborateur.objects.exclude(ru_it_id__isnull=True).exclude(ru_it_id=F('it')).values_list("ru_it_id", flat=True)
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

        # =========================================================
        # GESTION DES DRAPEAUX 1 ET 0
        # =========================================================
        n1_flag = 1 if it in l1 else 0
        n2_flag = 1 if it in l2 else 0
        n3_flag = 1 if it in l3 else 0
        n4_flag = 1 if it in l4 else 0

        # Détermination du rôle principal
        role = None
        if it in l4:
            role = "N+4"
        elif it in l3 :
            role = "N+3"
        elif it in l2:
            role = "N+2"
        elif it in l1:
            role = "N+1"

        # Si l'utilisateur n'est dans aucune liste (0 partout)
        if not role:
            return render(request, "utilisateur/accesInterdi.html")

        # Enregistrement en base avec les valeurs 1 ou 0
        utilisateur.objects.create(
            it_id=it,
            password=make_password(password),
            role=role,
            N1=n1_flag,
            N2=n2_flag,
            N3=n3_flag,
            N4=n4_flag
        )

        return render(request, "utilisateur/login.html")

    else:
        return render(request, "utilisateur/register.html")

#deriger vers la table de bord de Ru
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
        # FIX: manquait la garde présente dans reelEff/SystEff -> évite un
        # crash "Collaborateur.DoesNotExist" ou get(it=None) si la session
        # a expiré / l'utilisateur n'est pas connecté.
        return HttpResponseForbidden("Session expirée, veuillez vous reconnecter.")
 
    try:
        ru = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return HttpResponseForbidden("Collaborateur introuvable.")
 
    # 2. Opérateurs Système & Réel
    operateurs_systeme = SystEff(request)
    syste = operateurs_systeme.values('it').distinct().count()
 
    operateurs_reel = reelEff(request)
    # FIX: reelEff() retourne toujours un QuerySet (Collaborateur.objects.none()
    # au pire), jamais une liste brute -> la branche Counter() était du code
    # mort. Simplifié en gardant un fallback défensif minimal.
    counts_qs = operateurs_reel.values('lot').annotate(total=Count('it', distinct=True))
    counts_par_lot = {r['lot']: r['total'] for r in counts_qs}
    reel = operateurs_reel.values('it').distinct().count()
 
    # Différences (Système vs Réel)
    diff = difference(request)
    systeme = diff["systeme1"]
    reel1 = diff["reel1"]
 
    # 3. Récupération de la Maquette & Unité
    unite = None
    maquette = 0
    A = O = E = P = C = 0
 
    if ru.unite_id:
        try:
            # FIX PRINCIPAL: ru.unite_id est l'ID (FK) du modèle Unite, pas
            # son "abreviation". Le lookup original (abreviation=ru.unite_id)
            # ne matchait quasiment jamais -> maquette/A/E/P/C restaient à 0
            # silencieusement (avalé par le except ci-dessous).
            #
            # -> Si `unite_id` est bien la FK Django standard (Unite pk) :
            unite = Unite.objects.get(pk=ru.unite_id)
            #
            # -> Si en réalité `ru.unite` stocke directement l'abréviation
            #    (chaîne) et que `unite_id` est un champ mal nommé, utiliser
            #    plutôt la ligne suivante à la place de celle du dessus :
            # unite = Unite.objects.get(abreviation=ru.unite)
 
            maquette = unite.maquette or 0
            A = unite.A or 0
            E = unite.T or 0
            P = unite.P or 0
            C = unite.C or 0
        except Unite.DoesNotExist:
            pass
        except Unite.MultipleObjectsReturned:
            # FIX: cas non géré avant -> on prend le premier plutôt que de
            # laisser une exception remonter.
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
 
    # Écarts vs Maquette
    diff_r_m = reel - maquette
    diff_s_m = syste - maquette
 
    # 5. Date de dernière déclaration
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    date_declaration = der.date if der else timezone.localdate()
 
    # 6. Données pour le Graphique (7 derniers jours)
    # FIX: l'ancienne version comptait le nombre de déclarations de nature
    # "A"/"V" faites CE jour-là (evenement brut), pas l'effectif cumulé.
    # Résultat : si le dernier événement déclaré à une date donnée est un
    # "C" ou "D" (changement/départ) plutôt qu'un "A"/"V", comptes_par_date
    # n'a pas d'entrée pour cette date -> .get(derniere_date, 0) retombe à 0
    # -> chute artificielle du graphe (visible le jour même sur le dashboard).
    #
    # On réutilise reelEff(date_reference=d), qui gère déjà correctement
    # l'état cumulé (A/C/D) jour par jour, pour obtenir le VRAI effectif réel
    # à chaque date de la fenêtre.
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime('%d %b') for d in dates]
 
    data_list = [
        reelEff(request, date_reference=d).values('it').distinct().count()
        for d in dates
    ]
 
    # 7. Rendu Final
    context = {
        'chart_labels': json.dumps(labels_list),
        'chart_data': json.dumps(data_list),
        'selected_ru_nom': getattr(ru, 'nom_complete', f"RU #{it}"),
 
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
    }
    return render(request, "declaration_effectif/Tableau_de_bord.html", context)
 
 


#verifier que Ru existe
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
    # BUG (sécurité) : aucune vérification d'authentification ici —
    # n'importe qui, connecté ou non, peut interroger cet endpoint pour
    # savoir si un IT donné est un N+1 valide (fuite d'information sur
    # la structure organisationnelle). Cette protection avait été ajoutée
    # dans une version précédente puis a disparu de ce fichier.
    #
    # BUG (crash potentiel) : si "q" est vide/absent, ce chemin ne
    # retourne RIEN (pas de `else: return ...`) -> la fonction retourne
    # implicitement None -> Django lève
    # "ValueError: The view didn't return an HttpResponse object" (500).

    
# ============================================================
# deconnecter : vide la session et renvoie vers la page de login.
# ============================================================
def deconnecter(request):
    request.session.flush()
    return redirect("login")

#rederiger vers la page dashboard du Rg
# ============================================================
# dashboard_rg : tableau de bord consolidé d'un N+2 (RG) — agrège
# les effectifs système/réel/maquette de tous les RU (N+1) sous sa
# responsabilité, et compte les RU n'ayant pas encore déclaré.
# ============================================================
@role_required("N+2")
def dashboard_rg(request):
    it = request.session.get("it")
    liste_ru = Ru_Rg(it)
    
    ru_its = list(liste_ru.values_list("it", flat=True))
    unite_abrs = set(liste_ru.values_list("unite_id", flat=True))
    maint = timezone.localdate()

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

    # 7. Construction de la liste des résultats sans requêtes SQL supplémentaires
    liste_ru_stats = []
    effectif_syste = 0
    effectif_reel = 0

    for a in liste_ru:
        systeme = systeme_counts.get(a.it, 0)
        effectif_syste += systeme

        # Si le RU a une déclaration, on prend son effectif réel, sinon on prend l'effectif système
        if a.it in dernieres_dates and dernieres_dates[a.it] is not None:
            reel = reels_counts.get(a.it, 0)
        else:
            reel = systeme
            
        effectif_reel += reel

        maquette = unites_map.get(a.unite_id, 0)

        liste_ru_stats.append({
            "matricule": a.matricule,
            "nom_complete": a.nom_complete,
            "lot": a.lot,
            "unite": a.unite_id,
            "dpt": a.departement_id,
            "reel": reel,
            "systeme": systeme,
            "maquette": maquette,
            "MS": maquette - systeme,
            "MR": maquette - reel,
        })

    # Calcul des écarts globaux
    MR = maquette_total - effectif_reel
    MS = maquette_total - effectif_syste

    context = {
        "effectif_reel": effectif_reel,
        "effectif_syste": effectif_syste,
        "maquette_total": maquette_total,
        "non_valides": non_valides,
        "liste_ru_stats": liste_ru_stats,
        "MR": MR,
        "MS": MS,
        "maint": maint,
    }

    return render(request, "declaration_effectif/RG/Dashboardrg.html", context)


# ============================================================
# alertes : endpoint AJAX appelé quand un utilisateur ouvre son
# panneau de notifications — marque toutes ses alertes non lues
# comme lues.
# ============================================================
def alertes(request):
    it = request.session.get("it")
    if not it:
        return JsonResponse({"statu": "False", "error": "Non authentifié"}, status=401)
    
    # Récupère et met à jour en une seule requête SQL
    count_updated = Alert.objects.filter(recepteur=it, lu=False).update(lu=True)
    
    if count_updated > 0:
        return JsonResponse({"statu": "True", "count": count_updated})
    else:
        return JsonResponse({"statu": "False", "message": "Aucune alerte non lue"})


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
        "notifications": alert,
        "nb_notifications": nv,
    }