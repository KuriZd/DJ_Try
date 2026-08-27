import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.utils import html

from core.models import (
    Aspirante,
    Empresa,
    EstadoExpediente,
    EstadoReportePsicometrico,
    EstadoUsuario,
    EstadoVacante,
    PerfilProfesional,
    Postulacion,
    HistorialReportePsicometrico,
    OrigenReportePsicometrico,
    OrdenPagoPaypal,
    ReportePsicometrico,
    Rol,
    Usuario,
    UsuarioRol,
    Vacante,
)


def verify_password(password, encoded_password):
    try:
        if encoded_password.startswith("$argon2"):
            from argon2 import PasswordHasher

            return PasswordHasher().verify(encoded_password, password)
        if encoded_password.startswith(("$2a$", "$2b$", "$2y$")):
            import bcrypt

            return bcrypt.checkpw(
                password.encode("utf-8"),
                encoded_password.encode("utf-8"),
            )
        return check_password(password, encoded_password)
    except (ValueError, TypeError):
        return False
    except Exception:
        return False


class PerfilProfesionalSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerfilProfesional
        exclude = ("aspirante",)


class AspiranteSerializer(serializers.ModelSerializer):
    perfil_profesional = PerfilProfesionalSerializer(read_only=True)
    convocatoria_nombre = serializers.CharField(
        source="convocatoria.nombre", read_only=True
    )

    class Meta:
        model = Aspirante
        fields = (
            "id",
            "matricula",
            "nombre_completo",
            "fecha_nacimiento",
            "email",
            "telefono",
            "cedula_profesional",
            "direccion",
            "ciudad",
            "codigo_postal",
            "estado_region",
            "puesto_aspirado",
            "folio_aplicacion",
            "estado_expediente",
            "convocatoria",
            "convocatoria_nombre",
            "registrado_en",
            "actualizado_en",
            "perfil_profesional",
        )


class AspiranteResumenSerializer(serializers.ModelSerializer):
    """
    Identidad del aspirante dentro de una postulación.

    Es deliberadamente más corto que AspiranteSerializer: la lista de
    postulaciones no necesita el expediente completo (dirección, folio,
    convocatoria), y esos datos no tienen por qué viajar a una pantalla que
    sólo muestra el proceso de selección.
    """

    habilidades_tecnicas = serializers.SerializerMethodField()

    class Meta:
        model = Aspirante
        fields = ("id", "nombre_completo", "email", "habilidades_tecnicas")

    def get_habilidades_tecnicas(self, aspirante):
        # El perfil profesional es opcional: un expediente recién creado no lo
        # tiene todavía. RelatedObjectDoesNotExist hereda de AttributeError,
        # así que getattr con default cubre ese caso.
        perfil = getattr(aspirante, "perfil_profesional", None)
        return perfil.habilidades_tecnicas if perfil else []


class PostulacionSerializer(serializers.ModelSerializer):
    aspirante = AspiranteResumenSerializer(read_only=True)
    vacante_titulo = serializers.CharField(source="vacante.titulo", read_only=True)

    class Meta:
        model = Postulacion
        fields = (
            "id",
            "aspirante",
            "vacante",
            "vacante_titulo",
            "estado",
            "etapa",
            "progreso",
            "match_score",
            "experiencia_meses",
            "ultimo_empleo",
            "registrada_en",
            "ultima_actividad_en",
        )


class PostulacionCrearSerializer(serializers.ModelSerializer):
    """
    Alta de una postulación desde la bolsa de trabajo.

    El aspirante sale de la sesión y nunca del cuerpo: si viajara en el payload
    cualquiera podría postular a otra persona. `estado`, `etapa` y `progreso`
    tampoco se aceptan —una postulación nace en 'nuevo' y moverla por el
    proceso es trabajo del reclutador—, así que los toman los defaults del
    modelo.
    """

    class Meta:
        model = Postulacion
        fields = (
            "id",
            "vacante",
            "experiencia_meses",
            "ultimo_empleo",
            "expectativas_salariales",
            "horas_deseadas",
            "disponibilidad",
        )
        read_only_fields = ("id",)

    def validate_vacante(self, vacante):
        """
        Sólo se postula a lo que la bolsa pública ofrece.

        Se repiten aquí las condiciones de VacantePublicaViewSet: sin esto, un
        POST a mano podría colarse a un borrador o a una convocatoria cerrada,
        que en la vista pública ni siquiera aparecen.
        """
        ahora = timezone.now()

        esta_abierta = (
            vacante.estado == EstadoVacante.PUBLICADA
            and vacante.publicada_en is not None
            and vacante.publicada_en <= ahora
        )
        if not esta_abierta:
            raise serializers.ValidationError(
                "Esa vacante no está abierta a postulaciones."
            )

        if vacante.cierra_en is not None and vacante.cierra_en <= ahora:
            raise serializers.ValidationError(
                "La convocatoria de esa vacante ya cerró."
            )

        return vacante

    def validate_experiencia_meses(self, valor):
        if valor is not None and valor < 0:
            raise serializers.ValidationError(
                "La experiencia no puede ser negativa."
            )
        return valor

    def validate_horas_deseadas(self, valor):
        if valor is not None and valor < 1:
            raise serializers.ValidationError(
                "Las horas deseadas se cuentan por semana completa."
            )
        return valor

    def validate_expectativas_salariales(self, valor):
        if valor is not None and valor < 0:
            raise serializers.ValidationError(
                "La expectativa salarial no puede ser negativa."
            )
        return valor

    def validate_disponibilidad(self, valor):
        """La columna es JSONB libre; aquí se acota a una lista de textos."""
        if not isinstance(valor, list) or any(
            not isinstance(item, str) for item in valor
        ):
            raise serializers.ValidationError(
                "La disponibilidad se envía como una lista de textos."
            )
        return [item.strip() for item in valor if item.strip()]

    def validate(self, attrs):
        # El índice único (aspirante, vacante) ya lo impide, pero llegar por
        # aquí da un mensaje entendible en vez de un 500 por IntegrityError.
        # La carrera entre dos envíos simultáneos la atrapa la vista.
        if Postulacion.objects.filter(
            aspirante=self.context["expediente"], vacante=attrs["vacante"]
        ).exists():
            raise serializers.ValidationError(
                {"vacante": "Ya te postulaste a esta vacante."}
            )

        return attrs

    def create(self, validated_data):
        expediente = self.context["expediente"]
        perfil = getattr(expediente, "perfil_profesional", None)

        # Lo que el formulario deja en blanco se hereda del expediente: el
        # reclutador ve la experiencia declarada aunque el aspirante no la
        # haya repetido al postularse.
        if validated_data.get("experiencia_meses") is None and perfil is not None:
            validated_data["experiencia_meses"] = perfil.experiencia_meses

        validated_data["aspirante"] = expediente

        # Las columnas traen DEFAULT now() en el schema, pero Django las manda
        # en el INSERT: sin valor explícito viajarían como NULL.
        ahora = timezone.now()
        validated_data["registrada_en"] = ahora
        validated_data["ultima_actividad_en"] = ahora

        return super().create(validated_data)


class VacantePublicaSerializer(serializers.ModelSerializer):
    """Datos públicos que consume la card de vacantes del frontend."""

    area = serializers.CharField(source="departamento", read_only=True)
    resumen = serializers.CharField(source="descripcion", read_only=True)
    fecha_publicacion = serializers.SerializerMethodField()
    modalidad = serializers.SerializerMethodField()
    duracion_semanas = serializers.SerializerMethodField()

    class Meta:
        model = Vacante
        fields = (
            "id",
            "titulo",
            "empresa",
            "area",
            "estado",
            "fecha_publicacion",
            "resumen",
            "modalidad",
            "contratacion",
            "duracion_semanas",
            "email_contacto",
            "etiquetas",
            "requisitos",
        )

    def get_fecha_publicacion(self, vacante):
        if vacante.publicada_en is None:
            return None
        return timezone.localdate(vacante.publicada_en).isoformat()

    def get_modalidad(self, vacante):
        return {
            "presencial": "Presencial",
            "remoto": "Remota",
            "hibrido": "Híbrida",
        }.get(vacante.modalidad, vacante.modalidad)

    def get_duracion_semanas(self, vacante):
        return {
            "min": vacante.duracion_min_semanas,
            "max": vacante.duracion_max_semanas,
        }


class VacanteAdminSerializer(serializers.ModelSerializer):
    """
    Alta y edición de vacantes desde el panel interno.

    A diferencia de VacantePublicaSerializer expone los campos crudos del
    modelo —`departamento`, `descripcion`, el enum de modalidad— porque aquí
    se escribe, no se presenta.
    """

    class Meta:
        model = Vacante
        fields = (
            "id",
            "titulo",
            "empresa",
            "departamento",
            "descripcion",
            "modalidad",
            "jornada",
            "ciudad",
            "estado_region",
            "contratacion",
            "duracion_min_semanas",
            "duracion_max_semanas",
            "email_contacto",
            "etiquetas",
            "requisitos",
            "estado",
            "publicada_en",
            "cierra_en",
            "creado_en",
            "actualizado_en",
        )
        read_only_fields = ("id", "creado_en", "actualizado_en")

    def _valor(self, attrs, campo):
        if campo in attrs:
            return attrs[campo]
        return getattr(self.instance, campo, None)

    def validate(self, attrs):
        minimo = self._valor(attrs, "duracion_min_semanas")
        maximo = self._valor(attrs, "duracion_max_semanas")

        if minimo is not None and maximo is not None and minimo > maximo:
            raise serializers.ValidationError(
                {
                    "duracion_max_semanas": (
                        "La duración máxima no puede ser menor que la mínima."
                    )
                }
            )

        for campo in ("duracion_min_semanas", "duracion_max_semanas"):
            valor = attrs.get(campo)
            if valor is not None and valor < 1:
                raise serializers.ValidationError(
                    {campo: "La duración se cuenta en semanas completas."}
                )

        return attrs

    def create(self, validated_data):
        ahora = timezone.now()

        # El panel llama a esto "Publicar vacante": si no se pide otra cosa,
        # nace publicada en vez de quedarse en un borrador invisible.
        validated_data.setdefault("estado", EstadoVacante.PUBLICADA)
        validated_data.setdefault(
            "publicada_en", self._fecha_de_publicacion(validated_data, ahora)
        )

        # Las columnas traen DEFAULT now() en el schema, pero Django las manda
        # en el INSERT: sin valor explícito viajarían como NULL.
        validated_data["creado_en"] = ahora
        validated_data["actualizado_en"] = ahora

        return super().create(validated_data)

    def update(self, instance, validated_data):
        ahora = timezone.now()

        # Al publicar un borrador hay que fechar la publicación, o el listado
        # público lo seguiría filtrando pese al estado.
        if (
            validated_data.get("estado") == EstadoVacante.PUBLICADA
            and instance.publicada_en is None
            and not validated_data.get("publicada_en")
        ):
            validated_data["publicada_en"] = ahora

        validated_data["actualizado_en"] = ahora

        return super().update(instance, validated_data)

    @staticmethod
    def _fecha_de_publicacion(validated_data, ahora):
        """
        Sin `publicada_en` la consulta pública descarta la vacante, porque pide
        `publicada_en <= ahora`. Sólo se fecha lo que nace publicado.
        """
        if validated_data.get("estado") != EstadoVacante.PUBLICADA:
            return None
        return ahora


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    default_error_messages = {
        "invalid_credentials": "Correo o contraseña incorrectos.",
        "inactive": "El usuario no está activo.",
    }

    def validate(self, attrs):
        usuario = (
            Usuario.objects.filter(
                email__iexact=attrs["email"],
                eliminado_en__isnull=True,
            )
            .order_by("creado_en")
            .first()
        )

        # Se comprueba siempre un hash para reducir diferencias de tiempo.
        encoded_password = (
            usuario.password_hash
            if usuario
            else "pbkdf2_sha256$1$invalid$invalid"
        )
        password_is_valid = verify_password(attrs["password"], encoded_password)

        if usuario is None or not password_is_valid:
            self.fail("invalid_credentials")
        if not usuario.is_active:
            self.fail("inactive")

        attrs["usuario"] = usuario
        return attrs


class RegistroSerializer(serializers.Serializer):
    """
    Alta de cuenta desde el frontend público.

    Crea el usuario ya activo y su expediente de aspirante, para que pueda
    entrar y dar seguimiento a su proceso de inmediato.
    """

    nombre_completo = serializers.CharField(max_length=180)
    fecha_nacimiento = serializers.DateField(required=False, allow_null=True)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    # Reintentos por si dos altas simultáneas calculan el mismo consecutivo.
    INTENTOS_FOLIO = 3

    # Toda cuenta creada desde el frontend público es de un aspirante.
    ROL_POR_DEFECTO = "aspirante"

    def validate_nombre_completo(self, value):
        nombre = value.strip()
        if not nombre:
            raise serializers.ValidationError(
                "El nombre completo es obligatorio."
            )
        return nombre

    def validate_fecha_nacimiento(self, value):
        if value is not None and value > date.today():
            raise serializers.ValidationError(
                "La fecha de nacimiento no puede estar en el futuro."
            )
        return value

    def validate_email(self, value):
        email = value.strip()

        if Usuario.objects.filter(
            email__iexact=email, eliminado_en__isnull=True
        ).exists():
            raise serializers.ValidationError(
                "Ya existe una cuenta con ese correo."
            )

        # El expediente que se crea abajo comparte el correo y tiene su propio
        # índice único.
        if Aspirante.objects.filter(
            email__iexact=email, eliminado_en__isnull=True
        ).exists():
            raise serializers.ValidationError(
                "Ese correo ya está registrado en un expediente."
            )

        return email

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages))
        return value

    def _siguiente_consecutivo(self):
        """Consecutivo siguiente leyendo la parte numérica de los folios."""
        folios = Aspirante.objects.filter(id__regex=r"^ASP-\d+$").values_list(
            "id", flat=True
        )
        numeros = [int(folio.split("-")[1]) for folio in folios]
        return max(numeros, default=0) + 1

    def create(self, validated_data):
        ahora = timezone.now()

        usuario = Usuario.objects.create(
            id=uuid.uuid4(),
            nombre_completo=validated_data["nombre_completo"],
            email=validated_data["email"],
            password_hash=make_password(validated_data["password"]),
            estado=EstadoUsuario.ACTIVO,
            creado_en=ahora,
            actualizado_en=ahora,
        )

        rol = Rol.objects.filter(clave=self.ROL_POR_DEFECTO).first()
        if rol is None:
            raise serializers.ValidationError(
                {
                    "detail": "Falta el rol 'aspirante' en el catálogo. "
                    "Aplica las migraciones pendientes."
                }
            )
        UsuarioRol.objects.create(usuario=usuario, rol=rol, asignado_en=ahora)

        for intento in range(self.INTENTOS_FOLIO):
            consecutivo = self._siguiente_consecutivo()
            try:
                # Savepoint: si el folio choca, se deshace sólo este intento.
                with transaction.atomic():
                    Aspirante.objects.create(
                        id=f"ASP-{consecutivo:03d}",
                        usuario=usuario,
                        matricula=f"AM{ahora.year}-{consecutivo:04d}",
                        nombre_completo=usuario.nombre_completo,
                        fecha_nacimiento=validated_data.get("fecha_nacimiento"),
                        email=usuario.email,
                        estado_expediente=EstadoExpediente.INCOMPLETO,
                        registrado_en=ahora,
                        actualizado_en=ahora,
                    )
                break
            except IntegrityError:
                if intento == self.INTENTOS_FOLIO - 1:
                    raise serializers.ValidationError(
                        {
                            "detail": "No se pudo asignar la matrícula. "
                            "Intenta de nuevo."
                        }
                    )

        return usuario


def permisos_de(usuario):
    """Permisos efectivos del usuario: la unión de los de todos sus roles."""
    if usuario is None or not usuario.is_authenticated:
        return set()

    return {
        rol_permiso.permiso.clave
        for usuario_rol in usuario.usuarios_roles.select_related("rol")
        for rol_permiso in usuario_rol.rol.roles_permisos.select_related(
            "permiso"
        )
    }


class UsuarioSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    permisos = serializers.SerializerMethodField()
    aspirante = serializers.SerializerMethodField()
    empresa = serializers.SerializerMethodField()

    class Meta:
        model = Usuario
        fields = (
            "id",
            "nombre_completo",
            "email",
            "estado",
            "email_verificado_en",
            "ultimo_acceso_en",
            "creado_en",
            "actualizado_en",
            "roles",
            "permisos",
            "aspirante",
            "empresa",
        )

    def get_permisos(self, usuario):
        return sorted(permisos_de(usuario))

    def get_roles(self, usuario):
        return [
            {
                "id": usuario_rol.rol_id,
                "clave": usuario_rol.rol.clave,
                "nombre": usuario_rol.rol.nombre,
            }
            for usuario_rol in usuario.usuarios_roles.select_related("rol")
        ]

    def get_aspirante(self, usuario):
        try:
            aspirante = usuario.aspirante
        except Aspirante.DoesNotExist:
            return None

        if aspirante.eliminado_en is not None:
            return None

        return {
            "id": aspirante.id,
            "matricula": aspirante.matricula,
            "fecha_nacimiento": aspirante.fecha_nacimiento,
            "telefono": aspirante.telefono,
            "cedula_profesional": aspirante.cedula_profesional,
            "estado_expediente": aspirante.estado_expediente,
        }

    def get_empresa(self, usuario):
        try:
            empresa = usuario.empresa
        except Empresa.DoesNotExist:
            return None

        if empresa.eliminado_en is not None:
            return None

        return {
            "id": empresa.id,
            "razon_social": empresa.razon_social,
            "nombre_comercial": empresa.nombre_comercial,
            "rfc": empresa.rfc,
            "email_contacto": empresa.email_contacto,
            "telefono": empresa.telefono,
            "sitio_web": empresa.sitio_web,
            "sector": empresa.sector,
            "descripcion": empresa.descripcion,
            "direccion": empresa.direccion,
            "ciudad": empresa.ciudad,
            "estado_region": empresa.estado_region,
            "codigo_postal": empresa.codigo_postal,
            "logo_url": empresa.logo_url,
            "registrado_en": empresa.registrado_en,
            "actualizado_en": empresa.actualizado_en,
        }


class PerfilUpdateSerializer(serializers.Serializer):
    """Edición de los datos propios del usuario autenticado."""

    nombre_completo = serializers.CharField(max_length=180, required=False)
    email = serializers.EmailField(max_length=254, required=False)
    fecha_nacimiento = serializers.DateField(required=False, allow_null=True)
    telefono = serializers.CharField(
        max_length=30, required=False, allow_blank=True, allow_null=True
    )
    cedula_profesional = serializers.CharField(max_length=30, required=False)
    razon_social = serializers.CharField(max_length=180, required=False)
    nombre_comercial = serializers.CharField(
        max_length=180, required=False, allow_blank=True, allow_null=True
    )
    rfc = serializers.CharField(
        max_length=13, required=False, allow_blank=True, allow_null=True
    )
    email_contacto = serializers.EmailField(
        max_length=254, required=False, allow_blank=True, allow_null=True
    )
    sitio_web = serializers.URLField(
        max_length=255, required=False, allow_blank=True, allow_null=True
    )
    sector = serializers.CharField(
        max_length=120, required=False, allow_blank=True, allow_null=True
    )
    descripcion = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    direccion = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    ciudad = serializers.CharField(
        max_length=120, required=False, allow_blank=True, allow_null=True
    )
    estado_region = serializers.CharField(
        max_length=120, required=False, allow_blank=True, allow_null=True
    )
    codigo_postal = serializers.CharField(
        max_length=12, required=False, allow_blank=True, allow_null=True
    )
    logo_url = serializers.URLField(
        required=False, allow_blank=True, allow_null=True
    )

    def validate_nombre_completo(self, value):
        nombre = value.strip()
        if not nombre:
            raise serializers.ValidationError(
                "El nombre completo es obligatorio."
            )
        return nombre

    def validate_fecha_nacimiento(self, value):
        if value is not None and value > date.today():
            raise serializers.ValidationError(
                "La fecha de nacimiento no puede estar en el futuro."
            )
        return value

    def validate_email(self, value):
        email = value.strip()

        # Replica usuarios_email_unico: único sobre lower(email) entre los
        # usuarios no eliminados.
        if (
            Usuario.objects.filter(
                email__iexact=email, eliminado_en__isnull=True
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise serializers.ValidationError(
                "Ya existe un usuario con ese correo."
            )

        # El correo se replica al expediente, que tiene su propio índice único.
        try:
            aspirante = self.instance.aspirante
        except Aspirante.DoesNotExist:
            aspirante = None

        if (
            aspirante is not None
            and Aspirante.objects.filter(
                email__iexact=email, eliminado_en__isnull=True
            )
            .exclude(pk=aspirante.pk)
            .exists()
        ):
            raise serializers.ValidationError(
                "Ese correo ya está en uso por otro expediente."
            )

        return email

    def validate_cedula_profesional(self, value):
        cedula = value.strip()
        if not cedula:
            raise serializers.ValidationError(
                "La cédula profesional es obligatoria."
            )

        try:
            aspirante = self.instance.aspirante
        except Aspirante.DoesNotExist:
            raise serializers.ValidationError(
                "El usuario no tiene un aspirante relacionado."
            )

        # Sólo se puede registrar la primera vez. Corregir una cédula ya
        # asentada es un cambio de credencial oficial y pasa por revisión.
        if aspirante.cedula_profesional:
            raise serializers.ValidationError(
                "La cédula profesional ya está registrada; su cambio requiere "
                "revisión administrativa."
            )

        if (
            Aspirante.objects.filter(
                cedula_profesional__iexact=cedula, eliminado_en__isnull=True
            )
            .exclude(pk=aspirante.pk)
            .exists()
        ):
            raise serializers.ValidationError(
                "Esa cédula profesional ya está registrada en otro expediente."
            )

        return cedula

    def validate_razon_social(self, value):
        razon_social = value.strip()
        if not razon_social:
            raise serializers.ValidationError("La razón social es obligatoria.")
        return razon_social

    def validate_rfc(self, value):
        if not value:
            return value
        rfc = value.strip().upper()
        try:
            empresa = self.instance.empresa
        except Empresa.DoesNotExist:
            empresa = None
        if (
            Empresa.objects.filter(rfc__iexact=rfc, eliminado_en__isnull=True)
            .exclude(pk=empresa.pk if empresa else None)
            .exists()
        ):
            raise serializers.ValidationError(
                "Ese RFC ya está registrado por otra empresa."
            )
        return rfc

    @transaction.atomic
    def update(self, instance, validated_data):
        try:
            aspirante = instance.aspirante
        except Aspirante.DoesNotExist:
            aspirante = None

        try:
            empresa = instance.empresa
        except Empresa.DoesNotExist:
            empresa = None

        campos_expediente = {
            "fecha_nacimiento",
            "telefono",
            "cedula_profesional",
        }
        solicitados_sin_expediente = (
            campos_expediente.intersection(validated_data) - {"telefono"}
        )
        if solicitados_sin_expediente and aspirante is None:
            raise serializers.ValidationError(
                {
                    campo: "El usuario no tiene un aspirante relacionado."
                    for campo in solicitados_sin_expediente
                }
            )

        campos_empresa_editables = {
            "razon_social",
            "nombre_comercial",
            "rfc",
            "email_contacto",
            "sitio_web",
            "sector",
            "descripcion",
            "direccion",
            "ciudad",
            "estado_region",
            "codigo_postal",
            "logo_url",
        }
        solicitados_sin_empresa = campos_empresa_editables.intersection(
            validated_data
        )
        if solicitados_sin_empresa and empresa is None:
            raise serializers.ValidationError(
                {
                    campo: "El usuario no tiene una empresa relacionada."
                    for campo in solicitados_sin_empresa
                }
            )

        ahora = timezone.now()
        campos_usuario = []
        campos_aspirante = []
        campos_empresa = []

        if "nombre_completo" in validated_data:
            instance.nombre_completo = validated_data["nombre_completo"]
            campos_usuario.append("nombre_completo")
            if aspirante is not None:
                aspirante.nombre_completo = validated_data["nombre_completo"]
                campos_aspirante.append("nombre_completo")

        email_nuevo = validated_data.get("email")
        if email_nuevo and email_nuevo.lower() != instance.email.lower():
            instance.email = email_nuevo
            # La dirección nueva todavía no está verificada.
            instance.email_verificado_en = None
            campos_usuario.extend(["email", "email_verificado_en"])
            if aspirante is not None:
                # El expediente conserva el correo de contacto al que se envían
                # los certificados; si no se replica, quedaría desactualizado.
                aspirante.email = email_nuevo
                campos_aspirante.append("email")

        if "telefono" in validated_data:
            if aspirante is not None:
                aspirante.telefono = validated_data["telefono"]
                campos_aspirante.append("telefono")
            elif empresa is not None:
                empresa.telefono = validated_data["telefono"]
                campos_empresa.append("telefono")
            else:
                raise serializers.ValidationError(
                    {"telefono": "El usuario no tiene un perfil relacionado."}
                )

        if "fecha_nacimiento" in validated_data:
            aspirante.fecha_nacimiento = validated_data["fecha_nacimiento"]
            campos_aspirante.append("fecha_nacimiento")

        if "cedula_profesional" in validated_data:
            aspirante.cedula_profesional = validated_data["cedula_profesional"]
            campos_aspirante.append("cedula_profesional")

        for campo in campos_empresa_editables.intersection(validated_data):
            setattr(empresa, campo, validated_data[campo])
            campos_empresa.append(campo)

        if campos_usuario:
            instance.actualizado_en = ahora
            instance.save(update_fields=[*campos_usuario, "actualizado_en"])

        if campos_aspirante:
            aspirante.actualizado_en = ahora
            aspirante.save(
                update_fields=[*campos_aspirante, "actualizado_en"]
            )
        if campos_empresa:
            empresa.actualizado_en = ahora
            empresa.save(update_fields=[*campos_empresa, "actualizado_en"])
        return instance


class CambioPasswordSerializer(serializers.Serializer):
    """Cambio de contraseña verificando siempre la contraseña actual."""

    password_actual = serializers.CharField(
        write_only=True, trim_whitespace=False
    )
    password_nueva = serializers.CharField(
        write_only=True, trim_whitespace=False
    )

    def validate_password_actual(self, value):
        usuario = self.context["usuario"]
        if not verify_password(value, usuario.password_hash):
            raise serializers.ValidationError(
                "La contraseña actual es incorrecta."
            )
        return value

    def validate(self, attrs):
        if attrs["password_actual"] == attrs["password_nueva"]:
            raise serializers.ValidationError(
                {
                    "password_nueva": [
                        "La nueva contraseña debe ser distinta de la actual."
                    ]
                }
            )

        try:
            validate_password(attrs["password_nueva"], self.context["usuario"])
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                {"password_nueva": list(error.messages)}
            )

        return attrs

    def save(self, **kwargs):
        usuario = self.context["usuario"]
        usuario.password_hash = make_password(
            self.validated_data["password_nueva"]
        )
        usuario.actualizado_en = timezone.now()
        usuario.save(update_fields=["password_hash", "actualizado_en"])
        return usuario

PERMISO_ADMIN_REPORTES = "reportes-psicometricos:administrar"
PERMISO_SUBIR_REPORTE_PROPIO = "reportes-psicometricos:subir-propio"


class EscalaPsicometricaSerializer(serializers.Serializer):
    """Un factor del instrumento con el puntaje que obtuvo la persona."""

    nombre = serializers.CharField(max_length=120)
    puntaje = serializers.IntegerField(min_value=0, max_value=100)


class EscalasField(serializers.ListField):
    """Escalas del instrumento, tambien cuando llegan dentro de un multipart.

    La carga del archivo obliga a `multipart/form-data`, y ahi una lista de
    objetos no cabe: el cliente manda las escalas como una cadena JSON en un
    solo campo. `ListField` la tomaria con `getlist` y entregaria la cadena
    envuelta en una lista, asi que aqui se toma el valor crudo y se decodifica.
    """

    child = EscalaPsicometricaSerializer()

    def get_value(self, dictionary):
        if html.is_html_input(dictionary):
            return dictionary.get(self.field_name, serializers.empty)
        return super().get_value(dictionary)

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                raise serializers.ValidationError(
                    "Las escalas deben venir como una lista JSON."
                )
        return super().to_internal_value(data)


class ReportePsicometricoSerializer(serializers.ModelSerializer):
    """Reporte psicometrico del expediente.

    Sirve a dos flujos con reglas distintas: el administrador archiva el
    informe de una evaluacion aplicada por la plataforma, y el propio
    aspirante archiva el que trae de fuera. El origen no lo declara el
    cliente, lo decide el permiso con el que entra.
    """

    aspirante = serializers.PrimaryKeyRelatedField(
        queryset=Aspirante.objects.all(), required=False
    )
    aspirante_nombre = serializers.CharField(
        source="aspirante.nombre_completo", read_only=True
    )
    archivo = serializers.FileField(write_only=True)
    # Como se muestra en el expediente. Es opcional: quien no lo mande deja el
    # nombre del archivo, que es de donde salia antes de que se pudiera editar.
    nombre_original = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    escalas = EscalasField(required=False)

    class Meta:
        model = ReportePsicometrico
        fields = (
            "id",
            "aspirante",
            "aspirante_nombre",
            "referencia_evaluacion_externa",
            "archivo",
            "nombre_original",
            "mime_type",
            "tamano_bytes",
            "checksum_sha256",
            "precio",
            "moneda",
            "estado",
            "origen",
            "area_clave",
            "aplicada_en",
            "vigente_hasta",
            "puntaje",
            "nivel",
            "escalas",
            "paginas",
            "notas",
            "disponible_para_compra",
            "creado_en",
            "actualizado_en",
        )
        read_only_fields = (
            "id",
            "aspirante_nombre",
            "mime_type",
            "tamano_bytes",
            "checksum_sha256",
            "precio",
            "moneda",
            "estado",
            "origen",
            "disponible_para_compra",
            "creado_en",
            "actualizado_en",
        )

    def _permisos(self):
        return permisos_de(self.context["request"].user)

    def _es_administrador(self):
        return PERMISO_ADMIN_REPORTES in self._permisos()

    def _aspirante_del_usuario(self):
        aspirante = Aspirante.objects.filter(
            usuario=self.context["request"].user
        ).first()
        if aspirante is None:
            raise serializers.ValidationError(
                {"aspirante": "Tu cuenta todavia no tiene expediente de aspirante."}
            )
        return aspirante

    def validate_archivo(self, archivo):
        maximo = settings.REPORTE_PSICOMETRICO_MAX_MB * 1024 * 1024
        if archivo.size <= 0:
            raise serializers.ValidationError("El PDF esta vacio.")
        if archivo.size > maximo:
            raise serializers.ValidationError(
                f"El PDF no puede superar {settings.REPORTE_PSICOMETRICO_MAX_MB} MB."
            )

        encabezado = archivo.read(5)
        archivo.seek(0)
        if encabezado != b"%PDF-":
            raise serializers.ValidationError("El archivo no es un PDF valido.")

        content_type = getattr(archivo, "content_type", "")
        if content_type not in ("application/pdf", "application/x-pdf"):
            raise serializers.ValidationError(
                "El tipo de contenido debe ser application/pdf."
            )
        return archivo

    def validate_puntaje(self, puntaje):
        if puntaje is not None and not 0 <= puntaje <= 100:
            raise serializers.ValidationError("El puntaje va de 0 a 100.")
        return puntaje

    def validate_paginas(self, paginas):
        if paginas is not None and paginas < 0:
            raise serializers.ValidationError("Las paginas no pueden ser negativas.")
        return paginas

    def validate(self, attrs):
        # A nombre de quien se archiva. El aspirante no puede escribir en el
        # expediente de otro aunque mande su id en el formulario.
        if self._es_administrador():
            if attrs.get("aspirante") is None:
                raise serializers.ValidationError(
                    {"aspirante": "Indica a que aspirante pertenece el reporte."}
                )
        else:
            propio = self._aspirante_del_usuario()
            declarado = attrs.get("aspirante")
            if declarado is not None and declarado.pk != propio.pk:
                raise serializers.ValidationError(
                    {"aspirante": "Solo puedes archivar reportes en tu expediente."}
                )
            attrs["aspirante"] = propio

        aplicada_en = attrs.get("aplicada_en")
        vigente_hasta = attrs.get("vigente_hasta")
        ahora = timezone.now()
        if aplicada_en and aplicada_en > ahora:
            raise serializers.ValidationError(
                {"aplicada_en": "La fecha de aplicacion no puede estar en el futuro."}
            )
        if aplicada_en and vigente_hasta and vigente_hasta < aplicada_en:
            raise serializers.ValidationError(
                {
                    "vigente_hasta": (
                        "La vigencia no puede terminar antes de la aplicacion."
                    )
                }
            )
        return attrs

    def _reemplazar_misma_evaluacion(self, aspirante, referencia, ahora):
        """Marca como reemplazado el reporte previo de esa misma evaluacion.

        Solo aplica cuando la carga trae referencia externa: dos informes de
        evaluaciones distintas conviven en el archivero, pero volver a subir
        la misma evaluacion es una correccion, no un documento nuevo.
        """
        if not referencia:
            return []

        anteriores = list(
            ReportePsicometrico.objects.select_for_update().filter(
                aspirante=aspirante,
                referencia_evaluacion_externa=referencia,
                estado=EstadoReportePsicometrico.DISPONIBLE,
            )
        )
        ReportePsicometrico.objects.filter(
            id__in=[anterior.id for anterior in anteriores]
        ).update(
            estado=EstadoReportePsicometrico.REEMPLAZADO,
            disponible_para_compra=False,
            actualizado_en=ahora,
        )
        return anteriores

    @transaction.atomic
    def create(self, validated_data):
        archivo = validated_data["archivo"]
        checksum = hashlib.sha256()
        for bloque in archivo.chunks():
            checksum.update(bloque)
        archivo.seek(0)

        usuario = self.context["request"].user
        aspirante = validated_data["aspirante"]
        referencia = validated_data.get("referencia_evaluacion_externa")
        ahora = timezone.now()

        # Lo que sube el propio aspirante es su documento, no un informe que
        # la plataforma pueda vender.
        de_plataforma = self._es_administrador()
        origen = (
            OrigenReportePsicometrico.PLATAFORMA
            if de_plataforma
            else OrigenReportePsicometrico.PROPIA
        )

        # Bloquear el aspirante serializa incluso las dos primeras cargas
        # concurrentes, cuando todavia no existe un reporte que bloquear.
        Aspirante.objects.select_for_update().get(pk=aspirante.pk)
        anteriores = self._reemplazar_misma_evaluacion(aspirante, referencia, ahora)

        escalas = [dict(escala) for escala in validated_data.pop("escalas", [])]

        reporte = ReportePsicometrico.objects.create(
            id=uuid.uuid4(),
            subido_por=usuario,
            nombre_original=(
                validated_data.pop("nombre_original", "").strip()
                or archivo.name[:255]
            ),
            mime_type="application/pdf",
            tamano_bytes=archivo.size,
            checksum_sha256=checksum.hexdigest(),
            precio=Decimal(settings.REPORTE_PSICOMETRICO_PRECIO),
            moneda=settings.REPORTE_PSICOMETRICO_MONEDA.upper(),
            estado=EstadoReportePsicometrico.DISPONIBLE,
            origen=origen,
            escalas=escalas,
            disponible_para_compra=de_plataforma,
            creado_en=ahora,
            actualizado_en=ahora,
            **validated_data,
        )
        HistorialReportePsicometrico.objects.create(
            id=uuid.uuid4(),
            reporte=reporte,
            accion="uploaded",
            realizado_por=usuario,
            realizado_por_email=usuario.email,
            metadata={
                "nombre_original": reporte.nombre_original,
                "tamano_bytes": reporte.tamano_bytes,
                "checksum_sha256": reporte.checksum_sha256,
                "origen": reporte.origen,
            },
            creado_en=ahora,
        )
        HistorialReportePsicometrico.objects.bulk_create(
            [
                HistorialReportePsicometrico(
                    id=uuid.uuid4(),
                    reporte=anterior,
                    accion="replaced",
                    realizado_por=usuario,
                    realizado_por_email=usuario.email,
                    metadata={"reemplazado_por": str(reporte.id)},
                    creado_en=ahora,
                )
                for anterior in anteriores
            ]
        )
        return reporte


class CrearOrdenPagoPaypalSerializer(serializers.Serializer):
    reporte_id = serializers.UUIDField()

    def validate_reporte_id(self, reporte_id):
        try:
            reporte = ReportePsicometrico.objects.select_related(
                "aspirante"
            ).get(pk=reporte_id)
        except ReportePsicometrico.DoesNotExist:
            raise serializers.ValidationError("El reporte no existe.")

        usuario = self.context["request"].user
        if reporte.aspirante.usuario_id != usuario.id:
            # No confirmar a un usuario que existe el reporte de otra persona.
            raise serializers.ValidationError("El reporte no existe.")
        if (
            reporte.estado != EstadoReportePsicometrico.DISPONIBLE
            or reporte.origen != OrigenReportePsicometrico.PLATAFORMA
            or not reporte.disponible_para_compra
            or reporte.precio <= 0
        ):
            raise serializers.ValidationError(
                "El reporte no está disponible para compra."
            )
        self.context["reporte"] = reporte
        return reporte_id


class OrdenPagoPaypalSerializer(serializers.ModelSerializer):
    reporte_id = serializers.UUIDField(source="reporte.id", read_only=True)

    class Meta:
        model = OrdenPagoPaypal
        fields = (
            "referencia_interna",
            "reporte_id",
            "paypal_order_id",
            "monto",
            "moneda",
            "estado",
            "approval_url",
            "expira_en",
            "creado_en",
            "actualizado_en",
            "pagado_en",
        )
