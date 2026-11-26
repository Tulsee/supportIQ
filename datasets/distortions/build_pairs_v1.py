"""Builds pairs_v1.jsonl (D5): statements checked against the source sentence they claim
to restate, for research feature F6 (distortion-aware faithfulness checking).

Run from the repository root: python datasets/distortions/build_pairs_v1.py

Each source is a sentence (or two) from an agent's reply in datasets/knowledge_gaps or
from a Quillon document. Each has faithful restatements and distortions, written by hand:

- **role**: who acts or who is affected changes (the buyer for the seller, support for
  the customer, a direction reversed).
- **scope**: which product, plan, condition or thing changes, or the claim is widened.
- **polarity**: a negation is added or dropped, or a word turns into its opposite.
- **modality**: can, must, may, or "we'll find out" changes (an open question becomes
  a fact, a requirement becomes optional, a promise is added).
- **quantity**: a number, unit or bound changes, including numbers written as words and
  two numbers swapped.

``hard`` marks items a simple check is likely to get wrong: faithful restatements that
add a product name from the context or reword a negation, and distortions that keep
every number and name of the source. The split alternates by source, so no source
sentence appears in both dev and test. Ids are stable: append, never reorder.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
DOCS = ROOT / "sample_documents" / "quillon"
TICKETS = {
    t["id"]: t
    for t in (
        json.loads(line)
        for line in (ROOT / "knowledge_gaps" / "tickets_v1.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    )
}
TYPES = ("role", "scope", "polarity", "modality", "quantity")


def ticket(ticket_id: str) -> str:
    """What a drafting model sees of a ticket: subject, customer message, agent reply."""
    t = TICKETS[ticket_id]
    return f"{t['subject']}\n{t['message']}\n{t['reply']}"


def section(filename: str, heading: str) -> str:
    """One ``## heading`` section of a document, with the document's title."""
    text = (DOCS / filename).read_text("utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, (filename, heading)
    return f"{title}\n{heading}\n{match.group(1).strip()}"


@dataclass
class Source:
    origin: str  # "t08" or "doc:warranty-policy.md#Coverage"
    quote: str
    context: str
    faithful: list[tuple[bool, str]] = field(default_factory=list)  # (hard, statement)
    distorted: list[tuple[str, bool, str, str]] = field(default_factory=list)
    # (type, hard, statement, what changed)


def t(ticket_id: str, quote: str) -> Source:
    return Source(ticket_id, quote, ticket(ticket_id))


def d(filename: str, heading: str, quote: str) -> Source:
    return Source(f"doc:{filename}#{heading}", quote, section(filename, heading))


SOURCES: list[Source] = []


def add(source: Source, faithful: list, distorted: list) -> None:
    source.faithful = faithful
    source.distorted = distorted
    SOURCES.append(source)


# --- Agents' replies (knowledge_gaps/tickets_v1.jsonl) -----------------------------------

add(
    t("t01", "In the Quillon Home app, go to Settings, then Advanced, then Port forwarding, and tap Add rule."),
    [
        (False, "To add a port forwarding rule, open Settings, then Advanced, then Port forwarding in the Quillon Home app and tap Add rule."),
        (True, "Port forwarding rules for the Q3 are added in the Quillon Home app under Settings, then Advanced, then Port forwarding, with Add rule."),
    ],
    [
        ("role", False, "A support agent adds the port forwarding rule for you under Settings, then Advanced, then Port forwarding.", "the customer acts, not support"),
        ("scope", True, "In the Quillon Home app, go to Settings, then Wi-Fi, then Port forwarding, and tap Add rule.", "Advanced became Wi-Fi"),
    ],
)
add(
    t("t01", "The Q3 supports up to 20 port forwarding rules."),
    [
        (False, "Up to 20 port forwarding rules can be set on the Q3."),
        (True, "The Q3 allows no more than 20 port forwarding rules."),
    ],
    [
        ("quantity", False, "The Q3 supports up to 25 port forwarding rules.", "20 became 25"),
        ("quantity", True, "The Q3 supports at least 20 port forwarding rules.", "an upper bound became a lower bound"),
        ("scope", False, "The QC2 supports up to 20 port forwarding rules.", "Q3 became QC2"),
        ("polarity", False, "The Q3 does not support port forwarding rules.", "negation added"),
        ("modality", True, "The Q3 should support up to 20 port forwarding rules after the next update.", "a present fact became a future promise"),
    ],
)
add(
    t("t02", "No restart is needed: new port forwarding rules take effect within about a minute."),
    [
        (False, "New port forwarding rules start working within about a minute, without restarting the router."),
        (True, "You don't have to restart the Q3 after adding a port forwarding rule; it applies within roughly a minute."),
    ],
    [
        ("polarity", False, "A restart is needed before new port forwarding rules take effect.", "negation dropped"),
        ("quantity", True, "No restart is needed: new port forwarding rules take effect within about an hour.", "a minute became an hour"),
        ("modality", True, "No restart should be needed, but new port forwarding rules may take a while to work.", "a certainty became a hedge"),
    ],
)
add(
    t("t03", "When the microSD card is full, the QC2 overwrites the oldest recordings first, so it never stops recording."),
    [
        (False, "The QC2 keeps recording when its microSD card is full by overwriting the oldest recordings first."),
        (True, "A full microSD card doesn't stop the QC2 from recording: the oldest recordings are replaced first."),
    ],
    [
        ("scope", True, "When the microSD card is full, the QC2 overwrites the newest recordings first, so it never stops recording.", "oldest became newest"),
        ("polarity", False, "When the microSD card is full, the QC2 stops recording.", "never stops became stops"),
        ("role", True, "When the microSD card is full, you have to delete the oldest recordings so the QC2 keeps recording.", "the camera acts, not the customer"),
    ],
)
add(
    t("t03", "With continuous 1080p recording, a 128 GB card holds about 12 days of video"),
    [
        (False, "A 128 GB card stores roughly 12 days of continuous 1080p video."),
        (True, "In the QC2, about 12 days of continuous 1080p recording fit on a 128 GB card."),
    ],
    [
        ("quantity", False, "With continuous 1080p recording, a 128 GB card holds about 21 days of video.", "12 became 21"),
        ("scope", True, "With motion-only recording, a 128 GB card holds about 12 days of video.", "continuous became motion-only"),
        ("quantity", True, "With continuous 1080p recording, a 128 GB card holds about 12 hours of video.", "days became hours"),
    ],
)
add(
    t("t04", "You can check current outages at status.quillon.example, which is updated every 5 minutes during an incident."),
    [
        (False, "The status page at status.quillon.example shows current outages and is updated every 5 minutes during an incident."),
    ],
    [
        ("quantity", False, "You can check current outages at status.quillon.example, which is updated every 15 minutes during an incident.", "5 became 15"),
        ("scope", True, "You can check current outages and the status of your order at status.quillon.example.", "widened to orders"),
        ("modality", True, "Quillon plans to show current outages at status.quillon.example soon.", "an existing page became a plan"),
    ],
)
add(
    t("t05", "Products bought from a retailer have to be returned to that retailer, under the retailer's own return policy."),
    [
        (False, "Items purchased from a retailer are returned to that retailer, following the retailer's return policy."),
        (True, "A product bought from another store must go back to that store, under the store's own return policy."),
    ],
    [
        ("role", True, "Products bought from a retailer have to be returned to Quillon, under the retailer's own return policy.", "returned to Quillon, not the retailer"),
        ("modality", True, "Products bought from a retailer may be returned to that retailer, under the retailer's own return policy.", "have to became may"),
        ("polarity", False, "Products bought from a retailer don't have to be returned to that retailer.", "negation added"),
    ],
)
add(
    t("t05", "The Quillon warranty still applies to products bought from a retailer."),
    [
        (False, "Products bought from a retailer are still covered by the Quillon warranty."),
    ],
    [
        ("polarity", False, "The Quillon warranty no longer applies to products bought from a retailer.", "negation added"),
        ("scope", True, "The Quillon warranty applies to any Quillon product, whoever sold it and however many times it has changed hands.", "widened to every product and owner"),
        ("role", True, "The retailer's warranty, not Quillon's, applies to products bought from a retailer.", "whose warranty changed"),
    ],
)
add(
    t("t06", "The Q3 doesn't support Apple HomeKit at the moment."),
    [
        (False, "Apple HomeKit isn't supported by the Q3 at the moment."),
        (True, "The Q3 is not currently compatible with Apple HomeKit."),
    ],
    [
        ("polarity", False, "The Q3 supports Apple HomeKit.", "negation dropped"),
        ("modality", True, "The Q3 will support Apple HomeKit in a future update.", "a promise was added"),
        ("scope", True, "The Q3 doesn't support Amazon Alexa at the moment.", "HomeKit became Alexa (named elsewhere in the reply)"),
    ],
)
add(
    t("t06", "It works with Amazon Alexa and Google Home for voice commands, such as turning the guest network on or off."),
    [
        (True, "The Q3 works with Amazon Alexa and Google Home for voice commands."),
        (False, "Amazon Alexa and Google Home can be used for voice commands, for example to turn the guest network on or off."),
    ],
    [
        ("scope", False, "The Q3 works with Amazon Alexa and Siri for voice commands.", "Google Home became Siri"),
        ("polarity", False, "The Q3 doesn't work with Amazon Alexa or Google Home.", "negation added"),
    ],
)
add(
    t("t07", "In bridge mode your satellite nodes keep working, but parental controls, the guest network and Quillon Secure malware blocking are not available, because your other router manages the network."),
    [
        (True, "When the Q3 is in bridge mode, parental controls, the guest network and Quillon Secure malware blocking are unavailable."),
        (False, "Satellite nodes keep working in bridge mode."),
    ],
    [
        ("polarity", False, "In bridge mode, parental controls and the guest network remain available.", "not available became available"),
        ("scope", True, "In bridge mode, satellite nodes and parental controls stop working.", "satellite nodes moved to what stops"),
        ("role", True, "In bridge mode the Q3 manages the network, so parental controls are not available.", "the other router manages the network, not the Q3"),
    ],
)
add(
    t("t08", "Before you sell or give away a Q3, open the Quillon Home app, go to Devices, select the router and tap Remove device, then do a factory reset."),
    [
        (False, "Before selling or giving away a Q3, remove it in the Quillon Home app (Devices, select the router, Remove device), then factory reset it."),
        (True, "If you're selling your Q3, first tap Remove device for it under Devices in the Quillon Home app, and then reset it to factory settings."),
    ],
    [
        ("role", True, "Before selling or giving away a Q3 router, the buyer should open the Quillon Home app, go to Devices, select the router, and tap Remove device.", "the seller acts, not the buyer (seen in G1-G3)"),
        ("polarity", False, "You don't need to remove a Q3 from the Quillon Home app before you sell it.", "negation added"),
    ],
)
add(
    t("t08", "Quillon Secure subscriptions can't be transferred to another person: the new owner starts their own subscription."),
    [
        (False, "A Quillon Secure subscription cannot be transferred; the new owner needs their own."),
        (True, "The new owner of the router has to start their own Quillon Secure subscription, because yours can't move to them."),
    ],
    [
        ("scope", True, "Secure Plus subscriptions cannot be transferred to another person; the new owner must start their own subscription.", "Quillon Secure became Secure Plus (seen in G1-G3)"),
        ("polarity", False, "Quillon Secure subscriptions can be transferred to the new owner.", "negation dropped"),
        ("role", True, "Quillon Secure subscriptions can't be transferred: the seller has to start a new subscription for the buyer.", "the new owner starts it, not the seller"),
    ],
)
add(
    t("t08", "Remember to cancel yours under Account, then Subscription, if you no longer need it."),
    [
        (False, "If you no longer need your subscription, cancel it under Account, then Subscription."),
    ],
    [
        ("scope", True, "To transfer a Quillon Secure subscription, the customer must cancel their own subscription under Account, then Subscription.", "if no longer needed became to transfer (seen in G1-G3)"),
        ("role", True, "The new owner must cancel your subscription under Account, then Subscription.", "you cancel, not the new owner"),
        ("modality", True, "You must cancel your subscription under Account, then Subscription, before you sell the router.", "a conditional reminder became a requirement"),
    ],
)
add(
    t("t09", "You can change the delivery address yourself within 2 hours of placing the order"),
    [
        (False, "Within 2 hours of placing an order, you can change its delivery address yourself."),
    ],
    [
        ("quantity", False, "You can change the delivery address yourself within 24 hours of placing the order.", "2 became 24"),
        ("role", False, "Support can change the delivery address for you within 2 hours of placing the order.", "you act, not support"),
        ("modality", True, "You must change the delivery address within 2 hours of placing the order.", "can became must"),
        ("polarity", False, "You can't change the delivery address yourself within 2 hours of placing the order.", "negation added"),
    ],
)
add(
    t("t09", "After that, or once the order has shipped, contact the carrier with the tracking number from your shipping email."),
    [
        (False, "Once the order has shipped, contact the carrier using the tracking number in your shipping email."),
        (True, "After the first 2 hours, or once it has shipped, the carrier is the one to contact, with the tracking number from your shipping email."),
    ],
    [
        ("role", True, "Once the order has shipped, Quillon contacts the carrier for you with the tracking number.", "you contact the carrier, not Quillon"),
        ("scope", True, "Once the order has shipped, contact the carrier with the order number from your confirmation email.", "tracking number became order number"),
    ],
)
add(
    t("t10", "We don't offer student, teacher or military discounts at the moment."),
    [
        (False, "Quillon does not currently offer student, teacher or military discounts."),
        (True, "There are no student, teacher or military discounts at the moment."),
    ],
    [
        ("polarity", False, "Quillon offers student, teacher and military discounts.", "negation dropped"),
        ("modality", True, "Student, teacher and military discounts are being considered and may be offered soon.", "a plan was added"),
        ("scope", True, "We don't offer student discounts at the moment, but teacher and military discounts are available.", "narrowed to students only"),
    ],
)
add(
    t("t10", "The best saving is annual billing for Quillon Secure, which costs the same as 10 months."),
    [
        (False, "Annual billing for Quillon Secure is the best saving: it costs the same as 10 months."),
    ],
    [
        ("quantity", False, "The best saving is annual billing for Quillon Secure, which costs the same as 11 months.", "10 became 11"),
        ("scope", True, "The best saving is annual billing for Quillon Secure and the Q3 router, which costs the same as 10 months.", "widened to the router"),
    ],
)
add(
    t("t11", "The Q3 is a Wi-Fi 6E router with a combined maximum speed of 5400 Mbps across its three bands."),
    [
        (False, "The Q3's three bands together reach a maximum of 5400 Mbps; it is a Wi-Fi 6E router."),
    ],
    [
        ("scope", True, "The Q3 is a Wi-Fi 6E router with a maximum speed of 5400 Mbps on each of its three bands.", "combined became each band"),
        ("quantity", False, "The Q3 is a Wi-Fi 6E router with a combined maximum speed of 4800 Mbps.", "5400 became 4800"),
        ("scope", False, "The Q3 is a Wi-Fi 7 router with a combined maximum speed of 5400 Mbps.", "Wi-Fi 6E became Wi-Fi 7"),
    ],
)
add(
    t("t11", "Real-world speeds depend on your internet plan and how far your devices are from the router or the nearest node."),
    [
        (False, "Actual speeds depend on the internet plan and on the distance between your devices and the router or nearest node."),
    ],
    [
        ("polarity", False, "Real-world speeds don't depend on your internet plan.", "negation added"),
        ("scope", True, "Real-world speeds depend only on how many devices are connected.", "the factors changed"),
    ],
)
add(
    t("t14", "I've asked our engineering team about IPv6 on the Q3 and I'll get back to you as soon as I hear from them."),
    [
        (False, "The engineering team has been asked about IPv6 on the Q3."),
    ],
    [
        ("modality", False, "The Q3 does not support IPv6.", "an open question became a fact (seen in G1-G3)"),
        ("modality", True, "The Q3 supports IPv6.", "an open question became a fact"),
        ("role", True, "The engineering team asked the customer about IPv6 on the Q3.", "who asked whom reversed"),
    ],
)
add(
    t("t15", "On the sign-in screen of the Quillon Home app, tap Forgot password and enter your email address. The reset link is valid for 30 minutes."),
    [
        (False, "To reset your password, tap Forgot password on the app's sign-in screen and enter your email; the link you receive works for 30 minutes."),
    ],
    [
        ("quantity", True, "On the sign-in screen, tap Forgot password and enter your email address. The reset link is valid for 30 hours.", "minutes became hours"),
        ("quantity", False, "The password reset link is valid for 24 hours.", "30 minutes became 24 hours"),
        ("role", True, "A support agent resets your password from the sign-in screen; the link is valid for 30 minutes.", "you reset it, not support"),
    ],
)
add(
    t("t16", "A solid amber light means the router has no internet connection. Restart your modem first, wait two minutes, then restart the router."),
    [
        (False, "If the router's light is solid amber, it has no internet connection: restart the modem, wait two minutes, then restart the router."),
        (True, "Solid amber means no internet: restart the modem, wait 2 minutes, then restart the Q3."),
    ],
    [
        ("scope", True, "A flashing amber light means the router has no internet connection.", "solid became flashing"),
        ("quantity", True, "Restart your modem first, wait ten minutes, then restart the router.", "two minutes became ten"),
        ("polarity", False, "A solid amber light means the router is connected to the internet.", "no connection became connected"),
    ],
)
add(
    t("t20", "You can cancel at any time in the Quillon Home app under Account, then Subscription. You keep access until the end of the current billing period."),
    [
        (False, "You can cancel whenever you like under Account, then Subscription, in the Quillon Home app, and keep access until the current billing period ends."),
    ],
    [
        ("polarity", True, "If you cancel under Account, then Subscription, you lose access at once.", "keep access became lose access"),
        ("modality", True, "You can only cancel at the end of the current billing period.", "any time became only at the end"),
    ],
)
add(
    t("t23", "No, the QC2 doesn't need any open or forwarded ports. It only needs a 2.4 GHz Wi-Fi network, because it can't join a 5 GHz network."),
    [
        (False, "The QC2 needs no open or forwarded ports, only a 2.4 GHz Wi-Fi network, since it can't join 5 GHz."),
    ],
    [
        ("polarity", False, "The QC2 needs open or forwarded ports on your router.", "negation dropped"),
        ("scope", True, "The QC2 only needs a 5 GHz Wi-Fi network, because it can't join a 2.4 GHz network.", "2.4 GHz and 5 GHz swapped"),
        ("scope", False, "The Q3 doesn't need any open or forwarded ports.", "QC2 became Q3"),
    ],
)

# --- Documents (sample_documents/quillon) -------------------------------------------------

RETURNS = "returns-and-refunds-policy.md"
SECURE = "quillon-secure-plans.md"
WARRANTY = "warranty-policy.md"
ACCOUNT = "account-and-security.md"
CONTACT = "contacting-support.md"
SETUP = "q3-router-setup-guide.md"
QC2 = "qc2-camera-guide.md"
ERRORS = "error-codes-reference.md"
FIRMWARE = "firmware-release-notes.md"
FAQ = "help-center-faq.md"

add(
    d(RETURNS, "Returning a product", "You can return an unopened Quillon product within 30 days of delivery for a full refund."),
    [
        (False, "Unopened Quillon products can be returned within 30 days of delivery for a full refund."),
    ],
    [
        ("quantity", False, "You can return an unopened Quillon product within 60 days of delivery for a full refund.", "30 became 60"),
        ("scope", False, "You can return any Quillon product within 30 days of delivery for a full refund.", "unopened became any"),
        ("modality", True, "You must return an unopened Quillon product within 30 days of delivery.", "a right became an obligation"),
    ],
)
add(
    d(RETURNS, "Returning a product", "Opened products can be returned within 14 days of delivery, minus a 15% restocking fee."),
    [
        (False, "If a product has been opened, you can return it within 14 days of delivery, and a 15% restocking fee is deducted."),
    ],
    [
        ("quantity", True, "Opened products can be returned within 15 days of delivery, minus a 14% restocking fee.", "14 and 15 swapped"),
        ("polarity", False, "Opened products can be returned within 14 days of delivery without a restocking fee.", "the fee was dropped"),
        ("scope", True, "Refurbished products can be returned within 14 days of delivery, minus a 15% restocking fee.", "opened became refurbished"),
    ],
)
add(
    d(RETURNS, "Return shipping", "Return shipping is free for defective products. For any other return, the $8.95 cost of the prepaid label is deducted from your refund."),
    [
        (False, "Returning a defective product costs nothing; for other returns, $8.95 for the prepaid label comes out of the refund."),
    ],
    [
        ("scope", False, "Return shipping is free for every product.", "widened to every return"),
        ("role", True, "For any other return, you pay the carrier $8.95 when you hand over the parcel.", "deducted from the refund, not paid to the carrier"),
        ("quantity", False, "For any other return, the $9.85 cost of the prepaid label is deducted from your refund.", "8.95 became 9.85"),
    ],
)
add(
    d(RETURNS, "Refunds", "Refunds are issued to the original payment method within 5 to 7 business days after we receive the returned product."),
    [
        (False, "Once Quillon receives the returned product, the refund goes to the original payment method within 5 to 7 business days."),
    ],
    [
        ("quantity", True, "Refunds are issued to the original payment method within 5 to 7 calendar days after we receive the returned product.", "business days became calendar days"),
        ("scope", True, "Refunds are issued as store credit within 5 to 7 business days after we receive the returned product.", "original payment method became store credit"),
        ("role", True, "Refunds are issued within 5 to 7 business days after you send the product back.", "counted from Quillon receiving it, not from sending"),
    ],
)
add(
    d(SECURE, "Plans and prices", "Secure Plus costs $9.99 per month and adds 30 days of cloud video history for up to 4 cameras, plus VPN protection for up to 5 devices."),
    [
        (False, "For $9.99 a month, Secure Plus adds 30 days of cloud video history for up to 4 cameras and VPN protection for up to 5 devices."),
    ],
    [
        ("quantity", True, "Secure Plus costs $9.99 per month and adds 30 days of cloud video history for up to 5 cameras, plus VPN protection for up to 4 devices.", "4 and 5 swapped"),
        ("scope", True, "Secure Basic costs $9.99 per month and adds 30 days of cloud video history for up to 4 cameras.", "Plus became Basic (named elsewhere on the page)"),
        ("quantity", True, "Secure Plus costs $9.99 per year.", "per month became per year"),
    ],
)
add(
    d(SECURE, "Free trial", "Every new Q3 router includes a 30-day free trial of Secure Plus. You will not be charged if you cancel before the trial ends."),
    [
        (False, "New Q3 routers come with a free 30-day Secure Plus trial, and cancelling before it ends means you aren't charged."),
    ],
    [
        ("scope", True, "Every Q3 router, new or used, includes a 30-day free trial of Secure Plus.", "widened to used routers"),
        ("scope", False, "Every new QC2 camera includes a 30-day free trial of Secure Plus.", "Q3 router became QC2 camera"),
        ("polarity", False, "You will be charged even if you cancel before the trial ends.", "negation dropped"),
    ],
)
add(
    d(SECURE, "Changing your plan", "Upgrades take effect immediately, and you pay a prorated amount for the rest of the billing period. Downgrades take effect at the start of your next billing period."),
    [
        (False, "An upgrade applies right away with a prorated charge for the rest of the period, while a downgrade starts with the next billing period."),
    ],
    [
        ("scope", True, "Downgrades take effect immediately, and upgrades take effect at the start of your next billing period.", "upgrades and downgrades swapped"),
        ("role", True, "When you upgrade, Quillon pays you a prorated amount for the rest of the billing period.", "you pay, not Quillon"),
    ],
)
add(
    d(SECURE, "Cancelling", "Monthly plans are not refunded for partial months. If you cancel an annual plan within 14 days of purchase or renewal, you get a full refund."),
    [
        (False, "Partial months of a monthly plan aren't refunded; an annual plan cancelled within 14 days of purchase or renewal is fully refunded."),
    ],
    [
        ("polarity", False, "Monthly plans are refunded for partial months.", "negation dropped"),
        ("scope", True, "If you cancel a monthly plan within 14 days of purchase, you get a full refund.", "annual became monthly"),
        ("quantity", False, "If you cancel an annual plan within 30 days of purchase or renewal, you get a full refund.", "14 became 30"),
    ],
)
add(
    d(WARRANTY, "Coverage", "New Quillon products have a two-year limited warranty from the date of purchase. Refurbished products have a one-year limited warranty."),
    [
        (False, "The limited warranty lasts two years from purchase for new products and one year for refurbished ones."),
    ],
    [
        ("quantity", True, "New Quillon products have a three-year limited warranty from the date of purchase.", "two-year became three-year"),
        ("scope", True, "Refurbished products have a two-year limited warranty, and new products a one-year warranty.", "new and refurbished swapped"),
    ],
)
add(
    d(WARRANTY, "What is not covered", "The warranty is not transferable to a new owner."),
    [
        (False, "If the product changes hands, the warranty does not pass to the new owner."),
    ],
    [
        ("polarity", False, "The warranty transfers to a new owner.", "negation dropped"),
        ("modality", True, "The warranty can be transferred to a new owner for a fee.", "a possibility was added"),
    ],
)
add(
    d(WARRANTY, "Making a warranty claim", "If the claim is approved, we send a replacement and email you a prepaid label for returning the faulty device."),
    [
        (False, "Once a claim is approved, Quillon sends a replacement and emails a prepaid label to return the faulty device."),
    ],
    [
        ("role", True, "If the claim is approved, you pay for the label to return the faulty device.", "the label is prepaid by Quillon"),
        ("scope", True, "As soon as you make a claim, we send a replacement and email you a prepaid label.", "the approval condition was dropped"),
        ("modality", True, "If the claim is approved, we may send a replacement.", "we send became we may send"),
    ],
)
add(
    d(ACCOUNT, "Sharing access with your household", "The account owner can invite up to 5 household members under Account, then Household. Household members can view cameras and manage Wi-Fi, but only the owner can change the subscription or delete the account."),
    [
        (False, "Up to 5 household members can be invited by the account owner under Account, then Household; they can view cameras and manage Wi-Fi."),
    ],
    [
        ("role", True, "Household members can change the subscription or delete the account.", "only the owner can"),
        ("quantity", False, "The account owner can invite up to 6 household members.", "5 became 6"),
        ("role", True, "Household members can invite up to 5 other household members under Account, then Household.", "the owner invites, not members"),
    ],
)
add(
    d(ACCOUNT, "Deleting your account", "Your recorded video is deleted immediately. All other account data is deleted within 30 days."),
    [
        (False, "When you delete your account, recorded video is removed at once and the rest of your data within 30 days."),
    ],
    [
        ("scope", True, "Your recorded video is deleted within 30 days, and all other account data is deleted immediately.", "what is deleted when swapped"),
        ("modality", True, "Your recorded video may be kept after you delete your account.", "deleted became may be kept"),
    ],
)
add(
    d(CONTACT, "Ways to reach us", "Phone support is available Monday to Saturday, 7 a.m. to 9 p.m. Eastern Time. Chat support is available 24 hours a day, 7 days a week in the Quillon Home app."),
    [
        (False, "You can call support Monday to Saturday from 7 a.m. to 9 p.m. Eastern Time, and chat in the app at any hour of any day."),
    ],
    [
        ("scope", True, "Phone support is available Monday to Saturday, 7 a.m. to 9 p.m. Pacific Time.", "Eastern became Pacific"),
        ("scope", True, "Phone support is available 24 hours a day, 7 days a week.", "chat's hours given to phone"),
        ("scope", True, "Phone support is available Monday to Friday, 7 a.m. to 9 p.m. Eastern Time.", "Saturday became Friday"),
    ],
)
add(
    d(CONTACT, "Priority support", "Secure Family subscribers get priority support: their calls and chats are answered before others in the queue."),
    [
        (False, "Priority support for Secure Family subscribers means their calls and chats are answered first."),
    ],
    [
        ("scope", False, "Secure Plus subscribers get priority support.", "Family became Plus"),
        ("polarity", False, "Secure Family subscribers do not get priority support.", "negation added"),
    ],
)
add(
    d(SETUP, "Restarting and factory reset", "To restart the router, unplug the power adapter, wait 30 seconds, and plug it back in. Restarting keeps all of your settings."),
    [
        (False, "Restart the router by unplugging the power adapter for 30 seconds; your settings are kept."),
    ],
    [
        ("polarity", True, "To restart the router, unplug it for 30 seconds. Restarting erases all of your settings.", "keeps became erases"),
        ("quantity", True, "To restart the router, unplug the power adapter, wait 30 minutes, and plug it back in.", "seconds became minutes"),
    ],
)
add(
    d(SETUP, "Restarting and factory reset", "A factory reset erases your Wi-Fi name, password, guest network, and parental controls, and you will need to set up the router again in the app."),
    [
        (False, "After a factory reset you have to set the router up again in the app, because the Wi-Fi name, password, guest network and parental controls are erased."),
    ],
    [
        ("scope", True, "A factory reset erases your Wi-Fi name and password but keeps your guest network and parental controls.", "part of what is erased became kept"),
        ("modality", True, "After a factory reset, you might need to set up the router again in the app.", "will need became might need"),
    ],
)
add(
    d(SETUP, "Adding satellite nodes", "A Q3 network supports up to 6 satellite nodes in addition to the main router. Place each node no more than two rooms or about 10 metres from the router or from another node."),
    [
        (False, "Besides the main router, a Q3 network can have up to 6 satellite nodes, each within two rooms or about 10 metres of the router or another node."),
    ],
    [
        ("quantity", True, "A Q3 network supports up to 6 devices, the main router included.", "6 nodes besides the router became 6 in all"),
        ("quantity", True, "Place each node no more than four rooms from the router or from another node.", "two rooms became four"),
    ],
)
add(
    d(SETUP, "Guest network", "Devices on the guest network can reach the internet but cannot see devices on your main network."),
    [
        (False, "Guest network devices have internet access but can't see the devices on your main network."),
    ],
    [
        ("polarity", False, "Devices on the guest network can see devices on your main network.", "negation dropped"),
        ("role", True, "Devices on your main network cannot see devices on the guest network.", "the direction reversed"),
    ],
)
add(
    d(SETUP, "Automatic updates", "The Q3 checks for firmware updates every night and installs them automatically between 3 a.m. and 5 a.m. local time. You can postpone an update for up to 7 days in the app."),
    [
        (False, "Firmware updates are checked nightly and installed automatically between 3 and 5 a.m. local time; you can delay one for up to 7 days in the app."),
    ],
    [
        ("role", True, "You must install each firmware update yourself in the app within 7 days.", "the router installs them, not you"),
        ("quantity", True, "You can postpone an update for up to 7 weeks in the app.", "days became weeks"),
        ("polarity", False, "You cannot postpone a firmware update.", "negation added"),
    ],
)
add(
    d(QC2, "Setting up the camera", "The QC2 connects to 2.4 GHz Wi-Fi only; it cannot join a 5 GHz network."),
    [
        (False, "The QC2 works only on 2.4 GHz Wi-Fi and can't connect to a 5 GHz network."),
    ],
    [
        ("scope", True, "The QC2 connects to 5 GHz Wi-Fi only; it cannot join a 2.4 GHz network.", "2.4 GHz and 5 GHz swapped"),
        ("polarity", False, "The QC2 can join a 5 GHz network.", "negation dropped"),
    ],
)
add(
    d(QC2, "Privacy mode", "Turning on privacy mode in the app stops recording and turns off the microphone. The status light turns off while privacy mode is on."),
    [
        (False, "Privacy mode, turned on in the app, stops recording, switches off the microphone, and turns off the status light."),
    ],
    [
        ("polarity", True, "Privacy mode stops recording but leaves the microphone on.", "turns off became leaves on"),
        ("scope", True, "Turning on privacy mode stops recording and turns off the status light, but the camera keeps streaming live video.", "a claim was added"),
    ],
)
add(
    d(ERRORS, "Router and node errors", "E-4012: The cooling fan has stopped. Turn the router off and contact support to arrange a replacement under warranty. Do not leave the router running with a stopped fan."),
    [
        (False, "Error E-4012 means the cooling fan has stopped: switch the router off and contact support for a replacement under warranty."),
    ],
    [
        ("polarity", False, "With error E-4012 you can leave the router running with a stopped fan.", "do not became can"),
        ("role", True, "With error E-4012, support will contact you to arrange a replacement under warranty.", "you contact support, not the other way"),
        ("scope", True, "Error E-5020 means the cooling fan has stopped.", "E-4012 became E-5020 (named elsewhere in the section)"),
    ],
)
add(
    d(ERRORS, "Router and node errors", "E-7001: A firmware update failed its integrity check. The router automatically keeps its previous firmware. The update is retried after 30 minutes; if it fails three times in a row, contact support."),
    [
        (False, "With error E-7001 the router keeps its previous firmware and retries the update after 30 minutes; after three failures in a row, contact support."),
    ],
    [
        ("quantity", True, "If the update fails five times in a row, contact support.", "three became five"),
        ("role", True, "After error E-7001 you have to reinstall the previous firmware yourself.", "the router keeps it automatically"),
    ],
)
add(
    d(FIRMWARE, "How updates are installed", "Routers install new firmware automatically between 2 a.m. and 4 a.m. local time."),
    [
        (False, "New firmware is installed automatically between 2 and 4 a.m. local time."),
    ],
    [
        ("quantity", False, "Routers install new firmware automatically between 3 a.m. and 5 a.m. local time.", "2-4 became 3-5"),
        ("role", True, "You install new firmware yourself between 2 a.m. and 4 a.m. local time.", "the router installs it, not you"),
    ],
)
add(
    d(FAQ, "How many guests can use my guest network?", "Up to 16 devices can be connected to the guest network at once."),
    [
        (False, "The guest network accepts at most 16 devices at the same time."),
    ],
    [
        ("quantity", False, "Up to 32 devices can be connected to the guest network at once.", "16 became 32"),
        ("modality", True, "Up to 16 devices should be able to connect to the guest network at once, depending on the router.", "a fixed limit became a hedge"),
    ],
)


def build() -> list[dict]:
    rows = []
    number = 0
    for index, source in enumerate(SOURCES):
        assert source.quote in source.context, (source.origin, source.quote[:60])
        assert source.faithful and source.distorted, source.origin
        split = "dev" if index % 2 == 0 else "test"
        items = [("faithful", "none", hard, text, "") for hard, text in source.faithful] + [
            ("distorted", kind, hard, text, note) for kind, hard, text, note in source.distorted
        ]
        for label, kind, hard, text, note in items:
            assert kind in (*TYPES, "none"), kind
            number += 1
            rows.append(
                {
                    "id": f"d{number:03d}",
                    "source": source.origin,
                    "split": split,
                    "quote": source.quote,
                    "context": source.context,
                    "statement": text,
                    "label": label,
                    "type": kind,
                    "hard": hard,
                    "note": note,
                }
            )
    return rows


if __name__ == "__main__":
    rows = build()
    out = HERE / "pairs_v1.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    by = {}
    for r in rows:
        key = (r["split"], r["type"])
        by[key] = by.get(key, 0) + 1
    print(f"{len(rows)} pairs from {len(SOURCES)} sources -> {out.name}")
    for split in ("dev", "test"):
        print(split, {k: by.get((split, k), 0) for k in ("none", *TYPES)})
