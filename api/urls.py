from django.urls import path

from . import views, views_zonas

urlpatterns = [
    path("estado", views.estado),
    path("configuracion", views.configuracion_vista),

    path("auth/login", views.auth_login),
    path("auth/yo", views.auth_yo),
    path("auth/logout", views.auth_logout),

    path("ambiente/focos-historicos", views.ambiente_focos_historicos),
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

    # Validación externa (Rurrenabaque E122) repetida en vivo desde el dashboard
    path("validacion/rurrenabaque", views.validacion_rbq_contexto),
    path("validacion/rurrenabaque/ejecutar", views.validacion_rbq_ejecutar),
    path("validacion/rurrenabaque/ejecucion/<str:id_>", views.validacion_rbq_estado),
    # Validación multizona: cualquier municipio de Bolivia procesado con validacion_zonas/
    path("validacion/paquetes", views.validacion_paquetes),
    path("validacion/paquetes/<str:pid>", views.validacion_paquete_contexto),
    path("validacion/paquetes/<str:pid>/limite", views.validacion_paquete_limite),
    path("validacion/paquetes/<str:pid>/ejecutar", views.validacion_paquete_ejecutar),
    path("validacion/indice", views_zonas.indice),
    path("validacion/mapbiomas", views_zonas.mapbiomas_vista),
    path("validacion/zonas", views_zonas.zona_procesar),
    path("validacion/zonas/<str:mid>/<int:anio>", views_zonas.zona_detalle),
    path("validacion/zonas/<str:mid>/<int:anio>/paquete", views_zonas.paquete_preparar),
    path("validacion/paquetes/<str:pid>/oficial", views_zonas.paquete_oficial),
    path("validacion/trabajos", views_zonas.trabajos_vista),
    path("validacion/trabajos/<str:tid>", views_zonas.trabajos_vista),

    path("informes", views.informes_lista),
    path("informes/<str:id_>", views.informe_detalle),

    path("usuarios", views.usuarios_lista),
    path("usuarios/<str:id_>", views.usuario_detalle),
    path("usuarios/<str:id_>/restablecer-password", views.usuario_restablecer_password),

    path("permisos", views.permisos_vista),

    path("historicos", views.historicos),

    path("reportes-campo", views.reportes_campo),
    path("bitacora", views.bitacora_vista),
]
