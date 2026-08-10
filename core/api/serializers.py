import uuid
from datetime import date

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from core.models import (
    Aspirante,
    EstadoExpediente,
    EstadoUsuario,
    PerfilProfesional,
    Postulacion,
    Rol,
    Usuario,
    UsuarioRol,
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


class PerfilUpdateSerializer(serializers.Serializer):
    """Edición de los datos propios del usuario autenticado."""

    nombre_completo = serializers.CharField(max_length=180, required=False)
    email = serializers.EmailField(max_length=254, required=False)
    fecha_nacimiento = serializers.DateField(required=False, allow_null=True)
    telefono = serializers.CharField(
        max_length=30, required=False, allow_blank=True, allow_null=True
    )
    cedula_profesional = serializers.CharField(max_length=30, required=False)

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

    @transaction.atomic
    def update(self, instance, validated_data):
        try:
            aspirante = instance.aspirante
        except Aspirante.DoesNotExist:
            aspirante = None

        campos_expediente = {
            "fecha_nacimiento",
            "telefono",
            "cedula_profesional",
        }
        solicitados_sin_expediente = campos_expediente.intersection(validated_data)
        if solicitados_sin_expediente and aspirante is None:
            raise serializers.ValidationError(
                {
                    campo: "El usuario no tiene un aspirante relacionado."
                    for campo in solicitados_sin_expediente
                }
            )

        ahora = timezone.now()
        campos_usuario = []
        campos_aspirante = []

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
            aspirante.telefono = validated_data["telefono"]
            campos_aspirante.append("telefono")

        if "fecha_nacimiento" in validated_data:
            aspirante.fecha_nacimiento = validated_data["fecha_nacimiento"]
            campos_aspirante.append("fecha_nacimiento")

        if "cedula_profesional" in validated_data:
            aspirante.cedula_profesional = validated_data["cedula_profesional"]
            campos_aspirante.append("cedula_profesional")

        if campos_usuario:
            instance.actualizado_en = ahora
            instance.save(update_fields=[*campos_usuario, "actualizado_en"])

        if campos_aspirante:
            aspirante.actualizado_en = ahora
            aspirante.save(
                update_fields=[*campos_aspirante, "actualizado_en"]
            )
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
