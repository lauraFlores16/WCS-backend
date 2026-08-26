from django.urls import path

from . import views

urlpatterns = [
    path("estado", views.estado),
    path("configuracion", views.configuracion_vista),

    path("auth/login", views.auth_login),
    path("auth/yo", views.auth_yo),
    path("auth/logout", views.auth_logout),

    path("ambiente/meteo", views.ambiente_meteo),
    path("ambiente/climatologia", views.ambiente_climatologia),
    path("ambiente/firms", views.ambiente_firms),
    path("ambiente/terreno/osm", views.ambiente_terreno_osm),
    path("ambiente/terreno/dem", views.ambiente_terreno_dem),

    path("simulacion/parametros-auto", views.simulacion_parametros_auto),
    path("simulacion/ejecutar", views.simulacion_ejecutar),

    # Consola interactiva: pausar el autómata, inyectar un suceso del tiempo y
    # seguir desde ahí. Estas rutas van ANTES de "simulacion/<str:id_>", que si
    # no se tragaría "consola" como si fuera el id de un escenario.
    path("simulacion/escenarios", views.escenarios_catalogo),
    path("simulacion/consola", views.consola_iniciar),
    path("simulacion/consola/<str:id_>", views.consola_sesion),
    path("simulacion/consola/<str:id_>/avanzar", views.consola_avanzar),
    path("simulacion/consola/<str:id_>/inyectar", views.consola_inyectar),
    path("simulacion/consola/<str:id_>/guardar", views.consola_guardar),

    path("simulacion/<str:id_>", views.simulacion_detalle),

    path("escenarios", views.escenarios_lista),
    path("escenarios/<str:id_>", views.escenario_detalle),
    path("escenarios/<str:id_>/alertas", views.escenario_alertas),
    path("escenarios/<str:id_>/grafica", views.escenario_grafica),

    path("alertas/riesgo", views.alertas_riesgo),

    path("calibracion", views.calibracion_vista),

    path("informes", views.informes_lista),
    path("informes/<str:id_>", views.informe_detalle),

    path("usuarios", views.usuarios_lista),
    path("usuarios/<str:id_>", views.usuario_detalle),
    path("usuarios/<str:id_>/restablecer-password", views.usuario_restablecer_password),

    path("permisos", views.permisos_vista),

    path("historicos", views.historicos),

    path("bitacora", views.bitacora_vista),
]
