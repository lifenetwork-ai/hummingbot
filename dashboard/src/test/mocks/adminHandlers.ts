import { http, HttpResponse } from 'msw'
import { emptyOrders, pendingCommand, recordedOrders, staleBalances } from './adminFixtures'
import { credentialReceipt, reconciliationReceipt, recoveryCheckReceipt } from './receiptFixtures'

// Opt in with server.use(...adminHandlers); never installed in the running app.
export const ADMIN_MOCK_URL = 'https://admin.example.test/api/v1/admin'
export const adminHandlers = [
  http.get(`${ADMIN_MOCK_URL}/orders`, ({ request }) => HttpResponse.json(
    new URL(request.url).searchParams.get('cursor') ? emptyOrders : recordedOrders,
  )),
  http.get(`${ADMIN_MOCK_URL}/balances`, () => HttpResponse.json(staleBalances)),
  http.get(`${ADMIN_MOCK_URL}/commands/:id`, () => HttpResponse.json(pendingCommand)),
  http.get(`${ADMIN_MOCK_URL}/credentials/operations/:id`, ({ params }) => HttpResponse.json({ ...credentialReceipt, id: String(params.id) })),
  http.get(`${ADMIN_MOCK_URL}/reconciliation/:id`, ({ params }) => HttpResponse.json({ ...reconciliationReceipt, id: String(params.id) })),
  http.get(`${ADMIN_MOCK_URL}/recovery/operations/:id`, ({ params }) => HttpResponse.json({ ...recoveryCheckReceipt, id: String(params.id) })),
]
