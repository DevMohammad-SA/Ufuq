from django.db import migrations, models


def backfill_awards(apps, schema_editor):
    """Keep balances untouched; never infer task identity from ledger text.

    Accepted rows are known awards. Historical pending/rejected rows might
    have been accepted before reopening; no stable submission FK exists in
    the legacy ledger. NULL requires an explicit administrative decision
    before the next acceptance, rather than guessing from a task title.
    New submissions keep the field's False default.
    """
    alias = schema_editor.connection.alias
    Submission = apps.get_model('participants', 'TaskSubmission')
    Submission.objects.using(alias).filter(status='accepted').update(points_awarded=True)
    Submission.objects.using(alias).exclude(status='accepted').update(points_awarded=None)


class Migration(migrations.Migration):
    dependencies = [('participants', '0018_student_notes_and_review_permission')]
    operations = [
        migrations.AddField(
            model_name='tasksubmission', name='points_awarded',
            field=models.BooleanField(
                default=False, null=True, verbose_name='احتُسبت نقاط هذه المهمة',
                help_text='علامة دائمة تمنع تكرار المنح؛ القيمة غير المحددة تتطلب تحققًا إداريًا للسجل القديم.',
            ),
        ),
        migrations.RunPython(backfill_awards, migrations.RunPython.noop),
    ]
