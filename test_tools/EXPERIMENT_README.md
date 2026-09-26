# Synthetic Experiment Data JavaScript

generate_experiment_data.js generates data, downloads it, and imports it through the existing interfaces. No backend route or deployment code is added.

The default generates ten customer groups. Each has three synthetic emails, one product, one opportunity, one draft order, and one line item. It covers scenarios including unknown budget, urgent delivery, concern, and foreign currency. Every record uses synthetic markers and .example customer domains.

Generation remains reproducible through seed, baseTime, and version. Mail facts use the current extract-v6 format. source=synthetic_sample denotes manually constructed synthetic data rather than a real mailbox or model output. Draft orders are excluded from historical closed-deal statistics.

## Browser Execution

Sign in to the target SalesMate account, open the developer console, and paste the JavaScript file contents.

    const experiment = generateExperimentData({ count: 10, seed: 20260919 });
    // Save the original data first for verification and explicit reruns.
    downloadExperimentData(experiment);

Those statements do not import data. To save it:

    const receipt = await importExperimentData(experiment, {
      username: "tst1",
      mailboxId: "UUID of an existing mailbox for this account",
      agentToken: prompt("Temporary Agent service credential for this account, not a mailbox authorization code"),
      onProgress: progress => console.log(progress)
    });
    console.log(receipt);

After sign-in, the mailbox UUID is available from /api/v1/mailboxes/. Business writes use the current page session; mail interfaces accept only a separate Agent credential. A session, Tool token, or mailbox password cannot substitute for it. A developer with backend administration prepares the temporary service credential and revokes it after completion. Never send it in chat or commit it to Git.

Node.js may also require the same JavaScript and call its import function with baseUrl, mailboxId, agentToken, and toolToken. The Tool credential requires mailboxes.list, products.create, opportunities.create, orders.create, and order_lines.create. Node identity comes from the account bound to both credentials; browser mode additionally verifies the signed-in username. Both interfaces verify ownership of the specified mailbox.

## Import Behavior

- Emails are saved first and the backend creates real customers by domain. Other data uses returned real IDs; fixture IDs are never treated as server IDs.
- completed means the data write finished. Refresh the home page to view customers and mail, and the sales pages to view products, opportunities, and orders.
- Existing Workers generate profiles and scores asynchronously. Import completion does not mean analysis completion. The program does not send mail or turn draft orders into closed deals.
- Mail uses its natural deduplication key; business records use stable UUID idempotency keys. The same data, account, and mailbox may be rerun explicitly. Changed content for an old batch may conflict; a new experiment must use a new seed explicitly.
- On error, stop immediately without automatic retry. Completed HTTP requests are not rolled back. Retain the original JSON and receipt for verification and rerun from the same input.

## Verification

    node --test test_tools/tests/test_experiment_import.js

Offline mocks cover real-ID mapping, replay consistency, mailbox unauthorized access, and stopping writes on mail failure. Deployment acceptance must separately verify the data-owning account, database counts, and home-page query result.
