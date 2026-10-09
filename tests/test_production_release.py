"""Regresiones de producción incluidas en discovery habitual."""
from tests import production_audit_followup_checks as payments
from tests import qa_senior_20261004_checks as media


class PaymentReleaseTests(payments.PaymentFollowupChecks):
    pass


class RefundReleaseTests(payments.RefundConcurrencyChecks):
    pass


class MediaReleaseTests(media.SeniorMediaChecks):
    # Un 403 no demuestra que el objeto esté ausente. Se mantiene 503 hasta
    # conocer la política IAM real; 404 continúa siendo carga pendiente.
    def test_QAS03_cover_missing_without_list_bucket_must_report_pending_upload(self):
        from unittest.mock import patch
        from botocore.exceptions import ClientError
        from core.services import portadas_curso
        from tests.test_portadas_curso import s3_falso

        fake = s3_falso()
        fake.head_object.side_effect = ClientError({'Error': {'Code': '403'}}, 'HeadObject')
        key = portadas_curso.clave_nueva(self.course, 'image/jpeg')
        with patch('core.services.medios_publicacion.s3_video_storage', return_value=(fake, 'qa')):
            response = self.client.post(self.url, {'clave': key}, format='json')
        self.assertEqual(response.status_code, 503)
