import { http, HttpResponse } from 'msw'
import { emptyOrders, pendingCommand, recordedOrders, staleBalances } from './adminFixtures'

// Opt in with server.use(...adminHandlers); never installed in the running app.
export const ADMIN_MOCK_URL = 'https://admin.example.test/api/v1/admin'
export const adminHandlers = [
  http.get(`${ADMIN_MOCK_URL}/orders`, ({ request }) => HttpResponse.json(
    new URL(request.url).searchParams.get('cursor') ? emptyOrders : recordedOrders,
  )),
  http.get(`${ADMIN_MOCK_URL}/balances`, () => HttpResponse.json(staleBalances)),
  http.get(`${ADMIN_MOCK_URL}/commands/:id`, () => HttpResponse.json(pendingCommand)),
]
