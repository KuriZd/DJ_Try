from django.contrib.auth.hashers import check_password
from rest_framework import serializers

from core.models import (
    Aspirante,
    Certificado,
    Convocatoria,
    PerfilProfesional,
    Vacante,
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


class ConvocatoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Convocatoria
        fields = (
            "id",
            "nombre",
            "descripcion",
            "estado",
            "inicia_en",
            "termina_en",
            "creado_en",
            "actualizado_en",
        )


class VacanteSerializer(serializers.ModelSerializer):
    convocatoria_nombre = serializers.CharField(
        source="convocatoria.nombre", read_only=True
    )

    class Meta:
        model = Vacante
        fields = (
            "id",
            "convocatoria",
            "convocatoria_nombre",
            "titulo",
            "departamento",
            "descripcion",
            "modalidad",
            "jornada",
            "ciudad",
            "estado_region",
            "salario_min",
            "salario_max",
            "moneda",
            "estado",
            "publicada_en",
            "cierra_en",
            "creado_en",
            "actualizado_en",
        )


class CertificadoSerializer(serializers.ModelSerializer):
    aspirante_nombre = serializers.CharField(
        source="aspirante.nombre_completo", read_only=True
    )
    tipo_nombre = serializers.CharField(source="tipo.nombre", read_only=True)

    class Meta:
        model = Certificado
        fields = (
            "id",
            "folio",
            "codigo_verificacion",
            "aspirante",
            "aspirante_nombre",
            "aspirante_snapshot",
            "tipo",
            "tipo_nombre",
            "proceso",
            "proceso_nombre",
            "plantilla_id",
            "plantilla_version",
            "estado",
            "resultado",
            "periodo_participacion",
            "tipo_generacion",
            "autoridad_emisora",
            "cargo_autoridad",
            "emitido_en",
            "enviado_en",
            "creado_en",
            "actualizado_en",
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
