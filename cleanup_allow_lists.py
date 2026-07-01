import sys
import logging
import traceback

from server import create_app
from app.db import Session
from app.models import Alias, Contact

# Setup basic logging for the script
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("cleanup_allow_lists")


def main():
    log.info("Starting allow-list cleanup script...")

    app = create_app()
    with app.app_context():
        try:
            # Get all aliases that have a sender_allow_list configured
            aliases = Alias.filter(Alias.sender_allow_list.isnot(None)).all()
            log.info(f"Found {len(aliases)} aliases with a sender_allow_list.")

            updated_count = 0

            for alias in aliases:
                # We only care if there is an allow list
                current_allow_list = alias.get_sender_allow_domains()
                if not current_allow_list:
                    continue

                # Get all contacts for this alias
                contacts = Contact.filter_by(alias_id=alias.id).all()

                # To clean up correctly without accidentally allow-listing spammers (whose contacts might exist but aren't allow-listed),
                # we need to:
                # 1. Identify which contacts map to the *currently allow-listed* domains (which were based on envelope from)
                # 2. Extract the *new* valid domains from those specific contacts using the From field (website_email)

                new_allow_set = set()

                for contact in contacts:
                    email = contact.website_email
                    mail_from = contact.mail_from

                    # The old domain was strictly based on mail_from (envelope from)
                    old_domain = ""
                    if mail_from and mail_from != "<>" and "@" in mail_from:
                        old_domain = mail_from.split("@")[-1]

                    # The new domain is based on website_email, falling back to mail_from
                    email_to_extract = (
                        email
                        if email
                        else (mail_from if mail_from and mail_from != "<>" else "")
                    )
                    new_domain = (
                        email_to_extract.split("@")[-1]
                        if "@" in email_to_extract
                        else ""
                    )

                    # We ONLY care about contacts whose *old* domain is in the current allow-list,
                    # OR contacts whose *new* domain happens to already be in the current allow-list (in case they were manually added).
                    # If the contact was allowed, we carry over their *new* domain into the final allow-list.
                    if (old_domain and old_domain in current_allow_list) or (
                        new_domain and new_domain in current_allow_list
                    ):
                        if new_domain:
                            new_allow_set.add(new_domain)

                new_allow_list = list(new_allow_set)

                # Check for differences regardless of order
                if set(new_allow_list) != set(current_allow_list):
                    log.info(
                        f"Alias {alias.id} ({alias.email}): Updating allow-list from {current_allow_list} to {new_allow_list}"
                    )
                    alias.set_sender_allow_domains(new_allow_list)
                    Session.add(alias)
                    updated_count += 1

                    # Commit occasionally to not blow up the transaction log
                    if updated_count % 100 == 0:
                        Session.commit()
                        log.info(f"Committed {updated_count} alias updates so far...")

            # Final commit
            Session.commit()
            log.info(
                f"Allow-list cleanup completed successfully. Updated {updated_count} aliases."
            )

        except Exception as e:
            Session.rollback()
            log.error(f"Error during cleanup: {str(e)}")
            log.error(traceback.format_exc())
            sys.exit(1)


if __name__ == "__main__":
    main()
