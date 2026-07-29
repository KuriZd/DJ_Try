from django.db import models


class EstadoUsuario(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente"
    ACTIVO = "activo", "Activo"
    BLOQUEADO = "bloqueado", "Bloqueado"
    INACTIVO = "inactivo", "Inactivo"


class EstadoConvocatoria(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    PUBLICADA = "publicada", "Publicada"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"


class EstadoVacante(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    PUBLICADA = "publicada", "Publicada"
    PAUSADA = "pausada", "Pausada"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"


class ModalidadVacante(models.TextChoices):
    PRESENCIAL = "presencial", "Presencial"
    REMOTO = "remoto", "Remoto"
    HIBRIDO = "hibrido", "Híbrido"


class JornadaVacante(models.TextChoices):
    TIEMPO_COMPLETO = "tiempo_completo", "Tiempo completo"
    MEDIO_TIEMPO = "medio_tiempo", "Medio tiempo"
    ESTACIONAL = "estacional", "Estacional"


class EstadoPostulacion(models.TextChoices):
    NUEVO = "nuevo", "Nuevo"
    REVISION = "revision", "Revisión"
    SHORTLIST = "shortlist", "Lista corta"
    RECHAZADO = "rechazado", "Rechazado"
    CONTRATADO = "contratado", "Contratado"


class EstadoExpediente(models.TextChoices):
    ACTIVO = "activo", "Activo"
    INCOMPLETO = "incompleto", "Incompleto"
    SUSPENDIDO = "suspendido", "Suspendido"
    CERRADO = "cerrado", "Cerrado"


class EstadoCertificado(models.TextChoices):
    EN_PROCESO = "en_proceso", "En proceso"
    EMITIDO = "emitido", "Emitido"
    ENVIADO = "enviado", "Enviado"
    REENVIADO = "reenviado", "Reenviado"
    CANCELADO = "cancelado", "Cancelado"
    REVOCADO = "revocado", "Revocado"


class TipoGeneracion(models.TextChoices):
    AUTOMATICA = "automatica", "Automática"
    MANUAL = "manual", "Manual"


class EstadoEnvio(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente"
    ENVIADO = "enviado", "Enviado"
    FALLIDO = "fallido", "Fallido"


class TablaExistente(models.Model):
    """Base para tablas creadas por database/schema.sql."""

    class Meta:
        abstract = True
        managed = False


class Rol(TablaExistente):
    id = models.BigAutoField(primary_key=True)
    clave = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(null=True, blank=True)
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "roles"


class Permiso(TablaExistente):
    id = models.BigAutoField(primary_key=True)
    clave = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(null=True, blank=True)

    class Meta(TablaExistente.Meta):
        db_table = "permisos"


class RolPermiso(TablaExistente):
    pk = models.CompositePrimaryKey("rol", "permiso")
    rol = models.ForeignKey(
        Rol, models.CASCADE, db_column="rol_id", related_name="roles_permisos"
    )
    permiso = models.ForeignKey(
        Permiso, models.CASCADE, db_column="permiso_id", related_name="roles_permisos"
    )

    class Meta(TablaExistente.Meta):
        db_table = "roles_permisos"


class Usuario(TablaExistente):
    id = models.UUIDField(primary_key=True)
    nombre_completo = models.CharField(max_length=180)
    email = models.EmailField(max_length=254)
    password_hash = models.TextField()
    estado = models.CharField(
        max_length=20, choices=EstadoUsuario.choices, default=EstadoUsuario.PENDIENTE
    )
    email_verificado_en = models.DateTimeField(null=True, blank=True)
    ultimo_acceso_en = models.DateTimeField(null=True, blank=True)
    intentos_fallidos = models.SmallIntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField()
    actualizado_en = models.DateTimeField()
    eliminado_en = models.DateTimeField(null=True, blank=True)

    class Meta(TablaExistente.Meta):
        db_table = "usuarios"

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return self.estado == EstadoUsuario.ACTIVO


class UsuarioRol(TablaExistente):
    pk = models.CompositePrimaryKey("usuario", "rol")
    usuario = models.ForeignKey(
        Usuario, models.CASCADE, db_column="usuario_id", related_name="usuarios_roles"
    )
    rol = models.ForeignKey(
        Rol, models.PROTECT, db_column="rol_id", related_name="usuarios_roles"
    )
    asignado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "usuarios_roles"


class Sesion(TablaExistente):
    id = models.UUIDField(primary_key=True)
    usuario = models.ForeignKey(
        Usuario, models.CASCADE, db_column="usuario_id", related_name="sesiones"
    )
    refresh_token_hash = models.TextField(unique=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    expira_en = models.DateTimeField()
    revocada_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "sesiones"


class TokenRecuperacion(TablaExistente):
    id = models.UUIDField(primary_key=True)
    usuario = models.ForeignKey(
        Usuario,
        models.CASCADE,
        db_column="usuario_id",
        related_name="tokens_recuperacion",
    )
    token_hash = models.TextField(unique=True)
    expira_en = models.DateTimeField()
    usado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "tokens_recuperacion"


class Convocatoria(TablaExistente):
    id = models.CharField(primary_key=True, max_length=30)
    nombre = models.CharField(max_length=180)
    descripcion = models.TextField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=EstadoConvocatoria.choices,
        default=EstadoConvocatoria.BORRADOR,
    )
    inicia_en = models.DateTimeField(null=True, blank=True)
    termina_en = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="creado_por",
        null=True,
        blank=True,
        related_name="convocatorias_creadas",
    )
    creado_en = models.DateTimeField()
    actualizado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "convocatorias"


class Vacante(TablaExistente):
    id = models.BigAutoField(primary_key=True)
    convocatoria = models.ForeignKey(
        Convocatoria,
        models.SET_NULL,
        db_column="convocatoria_id",
        null=True,
        blank=True,
        related_name="vacantes",
    )
    titulo = models.CharField(max_length=180)
    departamento = models.CharField(max_length=120, null=True, blank=True)
    descripcion = models.TextField(null=True, blank=True)
    modalidad = models.CharField(max_length=20, choices=ModalidadVacante.choices)
    jornada = models.CharField(
        max_length=20,
        choices=JornadaVacante.choices,
        default=JornadaVacante.TIEMPO_COMPLETO,
    )
    ciudad = models.CharField(max_length=120, null=True, blank=True)
    estado_region = models.CharField(max_length=120, null=True, blank=True)
    salario_min = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    salario_max = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    moneda = models.CharField(max_length=3, default="MXN")
    estado = models.CharField(
        max_length=20, choices=EstadoVacante.choices, default=EstadoVacante.BORRADOR
    )
    publicada_en = models.DateTimeField(null=True, blank=True)
    cierra_en = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="creado_por",
        null=True,
        blank=True,
        related_name="vacantes_creadas",
    )
    creado_en = models.DateTimeField()
    actualizado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "vacantes"


class Aspirante(TablaExistente):
    id = models.CharField(primary_key=True, max_length=30)
    usuario = models.OneToOneField(
        Usuario,
        models.SET_NULL,
        db_column="usuario_id",
        null=True,
        blank=True,
        related_name="aspirante",
    )
    matricula = models.CharField(max_length=40, unique=True)
    nombre_completo = models.CharField(max_length=180)
    email = models.EmailField(max_length=254)
    telefono = models.CharField(max_length=30, null=True, blank=True)
    cedula_profesional = models.CharField(max_length=30, null=True, blank=True)
    direccion = models.TextField(null=True, blank=True)
    ciudad = models.CharField(max_length=120, null=True, blank=True)
    codigo_postal = models.CharField(max_length=12, null=True, blank=True)
    estado_region = models.CharField(max_length=120, null=True, blank=True)
    puesto_aspirado = models.CharField(max_length=180, null=True, blank=True)
    folio_aplicacion = models.CharField(
        max_length=40, unique=True, null=True, blank=True
    )
    estado_expediente = models.CharField(
        max_length=20,
        choices=EstadoExpediente.choices,
        default=EstadoExpediente.INCOMPLETO,
    )
    convocatoria = models.ForeignKey(
        Convocatoria,
        models.SET_NULL,
        db_column="convocatoria_id",
        null=True,
        blank=True,
        related_name="aspirantes",
    )
    registrado_en = models.DateTimeField()
    actualizado_en = models.DateTimeField()
    eliminado_en = models.DateTimeField(null=True, blank=True)

    class Meta(TablaExistente.Meta):
        db_table = "aspirantes"


class PerfilProfesional(TablaExistente):
    aspirante = models.OneToOneField(
        Aspirante,
        models.CASCADE,
        db_column="aspirante_id",
        primary_key=True,
        related_name="perfil_profesional",
    )
    nivel_educativo = models.CharField(max_length=100, null=True, blank=True)
    institucion = models.CharField(max_length=180, null=True, blank=True)
    carrera_especialidad = models.CharField(max_length=180, null=True, blank=True)
    certificaciones = models.JSONField(default=list)
    cursos_completados = models.JSONField(default=list)
    empresas = models.JSONField(default=list)
    puestos_anteriores = models.JSONField(default=list)
    experiencia_meses = models.IntegerField(null=True, blank=True)
    area_profesional = models.CharField(max_length=180, null=True, blank=True)
    habilidades_declaradas = models.JSONField(default=list)
    habilidades_tecnicas = models.JSONField(default=list)
    habilidades_blandas = models.JSONField(default=list)
    resultado_evaluaciones = models.TextField(null=True, blank=True)
    puntaje = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    compatibilidad_perfil = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    validacion_academica = models.BooleanField(default=False)
    validacion_laboral = models.BooleanField(default=False)
    validacion_competencias = models.BooleanField(default=False)
    actualizado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "perfiles_profesionales"


class Requisito(TablaExistente):
    clave = models.CharField(primary_key=True, max_length=50)
    nombre = models.CharField(max_length=140)
    descripcion = models.TextField(null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta(TablaExistente.Meta):
        db_table = "catalogo_requisitos"


class AspiranteRequisito(TablaExistente):
    pk = models.CompositePrimaryKey("aspirante", "requisito")
    aspirante = models.ForeignKey(
        Aspirante,
        models.CASCADE,
        db_column="aspirante_id",
        related_name="requisitos",
    )
    requisito = models.ForeignKey(
        Requisito,
        models.PROTECT,
        db_column="requisito_clave",
        related_name="aspirantes",
    )
    cumplido = models.BooleanField(default=False)
    validado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="validado_por",
        null=True,
        blank=True,
        related_name="requisitos_validados",
    )
    validado_en = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)

    class Meta(TablaExistente.Meta):
        db_table = "aspirantes_requisitos"


class Postulacion(TablaExistente):
    id = models.BigAutoField(primary_key=True)
    aspirante = models.ForeignKey(
        Aspirante,
        models.PROTECT,
        db_column="aspirante_id",
        related_name="postulaciones",
    )
    vacante = models.ForeignKey(
        Vacante,
        models.PROTECT,
        db_column="vacante_id",
        related_name="postulaciones",
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoPostulacion.choices,
        default=EstadoPostulacion.NUEVO,
    )
    etapa = models.CharField(max_length=120, default="Postulación recibida")
    progreso = models.SmallIntegerField(default=0)
    match_score = models.SmallIntegerField(null=True, blank=True)
    experiencia_meses = models.IntegerField(null=True, blank=True)
    ultimo_empleo = models.CharField(max_length=220, null=True, blank=True)
    expectativas_salariales = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    horas_deseadas = models.SmallIntegerField(null=True, blank=True)
    disponibilidad = models.JSONField(default=list)
    registrada_en = models.DateTimeField()
    ultima_actividad_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "postulaciones"
        constraints = [
            models.UniqueConstraint(
                fields=("aspirante", "vacante"),
                name="postulaciones_aspirante_vacante_uniq",
            )
        ]


class DocumentoAspirante(TablaExistente):
    id = models.UUIDField(primary_key=True)
    aspirante = models.ForeignKey(
        Aspirante,
        models.CASCADE,
        db_column="aspirante_id",
        related_name="documentos",
    )
    postulacion = models.ForeignKey(
        Postulacion,
        models.SET_NULL,
        db_column="postulacion_id",
        null=True,
        blank=True,
        related_name="documentos",
    )
    tipo = models.CharField(max_length=50)
    nombre_original = models.CharField(max_length=255)
    almacenamiento_clave = models.TextField()
    mime_type = models.CharField(max_length=100)
    tamano_bytes = models.BigIntegerField()
    checksum_sha256 = models.CharField(max_length=64, null=True, blank=True)
    subido_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "documentos_aspirante"


class TipoCertificado(TablaExistente):
    clave = models.CharField(primary_key=True, max_length=40)
    nombre = models.CharField(max_length=120)
    activo = models.BooleanField(default=True)

    class Meta(TablaExistente.Meta):
        db_table = "tipos_certificado"


class PlantillaCertificado(TablaExistente):
    pk = models.CompositePrimaryKey("id", "version")
    id = models.CharField(max_length=40)
    version = models.CharField(max_length=20)
    tipo = models.ForeignKey(
        TipoCertificado,
        models.PROTECT,
        db_column="tipo_clave",
        related_name="plantillas",
    )
    nombre = models.CharField(max_length=180)
    texto_institucional = models.TextField()
    configuracion = models.JSONField(default=dict)
    activa = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="creado_por",
        null=True,
        blank=True,
        related_name="plantillas_creadas",
    )
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "plantillas_certificado"


class Certificado(TablaExistente):
    id = models.CharField(primary_key=True, max_length=60)
    folio = models.CharField(max_length=40, unique=True)
    codigo_verificacion = models.CharField(max_length=64, unique=True)
    aspirante = models.ForeignKey(
        Aspirante,
        models.SET_NULL,
        db_column="aspirante_id",
        null=True,
        blank=True,
        related_name="certificados",
    )
    aspirante_snapshot = models.JSONField()
    tipo = models.ForeignKey(
        TipoCertificado,
        models.PROTECT,
        db_column="tipo_clave",
        related_name="certificados",
    )
    proceso = models.ForeignKey(
        Convocatoria,
        models.SET_NULL,
        db_column="proceso_id",
        null=True,
        blank=True,
        related_name="certificados",
    )
    proceso_nombre = models.CharField(max_length=180)
    plantilla_id = models.CharField(max_length=40)
    plantilla_version = models.CharField(max_length=20)
    estado = models.CharField(
        max_length=20,
        choices=EstadoCertificado.choices,
        default=EstadoCertificado.EN_PROCESO,
    )
    resultado = models.TextField(null=True, blank=True)
    periodo_participacion = models.CharField(max_length=100, null=True, blank=True)
    tipo_generacion = models.CharField(
        max_length=20,
        choices=TipoGeneracion.choices,
        default=TipoGeneracion.AUTOMATICA,
    )
    justificacion_manual = models.TextField(null=True, blank=True)
    autoridad_emisora = models.CharField(max_length=180, null=True, blank=True)
    cargo_autoridad = models.CharField(max_length=180, null=True, blank=True)
    observaciones_internas = models.TextField(null=True, blank=True)
    emitido_en = models.DateTimeField(null=True, blank=True)
    emitido_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="emitido_por",
        null=True,
        blank=True,
        related_name="certificados_emitidos",
    )
    certificado_anterior = models.ForeignKey(
        "self",
        models.SET_NULL,
        db_column="certificado_anterior_id",
        null=True,
        blank=True,
        related_name="certificados_siguientes",
    )
    reemplazado_por = models.ForeignKey(
        "self",
        models.SET_NULL,
        db_column="reemplazado_por_id",
        null=True,
        blank=True,
        related_name="certificados_reemplazados",
    )
    cancelado_en = models.DateTimeField(null=True, blank=True)
    cancelado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="cancelado_por",
        null=True,
        blank=True,
        related_name="certificados_cancelados",
    )
    motivo_cancelacion = models.TextField(null=True, blank=True)
    revocado_en = models.DateTimeField(null=True, blank=True)
    revocado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="revocado_por",
        null=True,
        blank=True,
        related_name="certificados_revocados",
    )
    motivo_revocacion = models.TextField(null=True, blank=True)
    enviado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField()
    actualizado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "certificados"


class HistorialCertificado(TablaExistente):
    id = models.UUIDField(primary_key=True)
    certificado = models.ForeignKey(
        Certificado,
        models.CASCADE,
        db_column="certificado_id",
        related_name="historial",
    )
    accion = models.CharField(max_length=50)
    estado_anterior = models.CharField(
        max_length=20, choices=EstadoCertificado.choices, null=True, blank=True
    )
    estado_nuevo = models.CharField(
        max_length=20, choices=EstadoCertificado.choices, null=True, blank=True
    )
    realizado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="realizado_por",
        null=True,
        blank=True,
        related_name="acciones_certificados",
    )
    realizado_por_email = models.EmailField(max_length=254, null=True, blank=True)
    descripcion = models.TextField()
    metadata = models.JSONField(default=dict)
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "historial_certificados"


class EnvioCertificado(TablaExistente):
    id = models.UUIDField(primary_key=True)
    certificado = models.ForeignKey(
        Certificado,
        models.CASCADE,
        db_column="certificado_id",
        related_name="envios",
    )
    destinatario_email = models.EmailField(max_length=254)
    enviado_por = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="enviado_por",
        null=True,
        blank=True,
        related_name="envios_certificados",
    )
    estado = models.CharField(
        max_length=20, choices=EstadoEnvio.choices, default=EstadoEnvio.PENDIENTE
    )
    numero_intento = models.SmallIntegerField(default=1)
    proveedor_id = models.CharField(max_length=180, null=True, blank=True)
    mensaje_error = models.TextField(null=True, blank=True)
    enviado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "envios_certificado"


class Auditoria(TablaExistente):
    id = models.BigAutoField(primary_key=True)
    usuario = models.ForeignKey(
        Usuario,
        models.SET_NULL,
        db_column="usuario_id",
        null=True,
        blank=True,
        related_name="auditorias",
    )
    accion = models.CharField(max_length=100)
    entidad = models.CharField(max_length=80)
    entidad_id = models.TextField(null=True, blank=True)
    datos_anteriores = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    creado_en = models.DateTimeField()

    class Meta(TablaExistente.Meta):
        db_table = "auditoria"
