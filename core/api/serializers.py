from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from core.models import (
    Aspirante,
    PerfilProfesional,
    Usuario,
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


class UsuarioSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
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
            "aspirante",
        )

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
            "telefono": aspirante.telefono,
            "cedula_profesional": aspirante.cedula_profesional,
            "estado_expediente": aspirante.estado_expediente,
        }


class PerfilUpdateSerializer(serializers.Serializer):
    """Edición de los datos propios del usuario autenticado."""

    nombre_completo = serializers.CharField(max_length=180, required=False)
    telefono = serializers.CharField(
        max_length=30, required=False, allow_blank=True, allow_null=True
    )

    def validate_nombre_completo(self, value):
        nombre = value.strip()
        if not nombre:
            raise serializers.ValidationError(
                "El nombre completo es obligatorio."
            )
        return nombre

    @transaction.atomic
    def update(self, instance, validated_data):
        try:
            aspirante = instance.aspirante
        except Aspirante.DoesNotExist:
            aspirante = None

        if "telefono" in validated_data and aspirante is None:
            raise serializers.ValidationError(
                {"telefono": "El usuario no tiene un aspirante relacionado."}
            )

        ahora = timezone.now()
        if "nombre_completo" in validated_data:
            instance.nombre_completo = validated_data["nombre_completo"]
            instance.actualizado_en = ahora
            instance.save(update_fields=["nombre_completo", "actualizado_en"])
            if aspirante is not None:
                aspirante.nombre_completo = validated_data["nombre_completo"]

        campos_aspirante = []
        if "nombre_completo" in validated_data and aspirante is not None:
            campos_aspirante.append("nombre_completo")
        if "telefono" in validated_data:
            aspirante.telefono = validated_data["telefono"]
            campos_aspirante.append("telefono")
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
