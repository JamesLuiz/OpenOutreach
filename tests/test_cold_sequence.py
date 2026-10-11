"""What `run` hands the sender is still mailed as cold outreach: an opener, two follow-ups, the end.

The sender's built-in cold sequence is one step plus follow-ups, on by default. This is the
host's side of that promise — the stream `run` builds lands in the sender as a cold deal, and
`send` on later days chases it twice and then closes it — asserted through the two verbs an
operator types, with only the boundaries stubbed: the finder, the model, SMTP and IMAP.
"""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db.models import F

from openoutreach import __main__ as cli

LEAD = {"lead_id": "lead-1", "email": "sam@example.com", "first_name": "Sam",
        "company": "Example Co"}

pytestmark = pytest.mark.django_db


@pytest.fixture
def sender():
    """The sender with its boundaries stubbed, and every email it wrote, in order."""
    from openoutsend.core.agents.outreach import OutreachDecision
    from openoutsend.core.operator import set_operator
    from openoutsend.emails.models import Mailbox

    set_operator(full_name="Ada Operator")
    Mailbox.objects.create(username="ada@corp.com", password="pw",
                           from_address="ada@corp.com", daily_limit=10)
    written = []

    def write(deal, prompt, *, opener=False):
        written.append(prompt)
        return OutreachDecision(action="send_message", message=f"Email {len(written)}.",
                                subject="A question" if opener else None)

    with patch("openoutsend.first_run.check_ready"), \
            patch("openoutsend.emails.mail_pass.run_mail_pass", return_value=(0, 0, 0)), \
            patch("openoutsend.emails.warmth.measure_pool"), \
            patch("openoutsend.core.sending_window.within_sending_window", return_value=True), \
            patch("openoutsend.leads.summaries.materialize_profile_summary_if_missing"), \
            patch("openoutsend.core.agents.outreach.run_outreach_agent", side_effect=write), \
            patch("openoutsend.emails.sender._deliver"):
        yield written


def _find_one_lead(name, *args, stdout, **kwargs):
    """The finder's `find --json`, answering with one lead."""
    stdout.write(json.dumps(LEAD) + "\n")


def _two_weeks_pass():
    """Age the mail log, so the next `send` runs as if ten working days had gone by."""
    from openoutsend.emails.models import Mailbox, Message

    Message.objects.update(sent_at=F("sent_at") - timedelta(days=14))
    Mailbox.objects.update(next_send_at=None)


def _deal():
    from openoutsend.leads.models import Deal

    return Deal.objects.get(lead__email=LEAD["email"])


def test_run_then_send_mails_the_opener_and_its_two_follow_ups(sender):
    from openoutsend.emails.models import Direction
    from openoutsend.emails.steps.follow_up import FOLLOW_UP_PROMPT
    from openoutsend.leads.models import DealState

    with patch("openoutreach.wizard.onboard"), \
            patch("django.core.management.call_command", side_effect=_find_one_lead):
        assert cli._run([]) == 0
    deal = _deal()
    assert (deal.sequence, deal.step, deal.state) == ("cold", 0, DealState.EMAILED)
    assert len(sender) == 1

    for _ in range(2):
        _two_weeks_pass()
        assert cli._send([]) == 0

    assert sender[1:] == [FOLLOW_UP_PROMPT, FOLLOW_UP_PROMPT]
    assert deal.thread.messages.filter(direction=Direction.OUTBOUND).count() == 3

    _two_weeks_pass()
    assert cli._send([]) == 0

    deal = _deal()
    assert (deal.state, deal.outcome) == (DealState.COMPLETED, "unresponsive")
    assert len(sender) == 3


def test_a_send_on_the_same_day_does_not_chase(sender):
    """The first follow-up waits for its gap; a second `send` straight after mails nothing."""
    with patch("openoutreach.wizard.onboard"), \
            patch("django.core.management.call_command", side_effect=_find_one_lead):
        cli._run([])

    assert cli._send([]) == 0

    assert len(sender) == 1
