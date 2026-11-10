/** Generated from docs/api/openapi.json. Run pnpm run api:generate; do not edit. */
export interface paths {
    "/api/v1/admin/access/accounts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List approved accounts and roles */
        get: operations["listAdminAccounts"];
        put?: never;
        /** Approve one Google account and assign its role */
        post: operations["addAdminAccount"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/access/accounts/{account_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Change an approved account role or disable its access */
        post: operations["changeAdminAccount"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/access/bootstrap": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Check readiness before disabling bootstrap password sign-in */
        get: operations["getAdminBootstrap"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/access/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sign in with verified Google identity or enabled bootstrap password */
        post: operations["adminLogin"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/access/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read your current role and permissions */
        get: operations["getAdminIdentity"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read security history: who changed access or connections and when */
        get: operations["getAdminAudit"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/balances": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read account cash and equity once, with observation freshness */
        get: operations["getBalances"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/commands": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Find previous session-control receipts after reload or operator handover */
        get: operations["listCommands"];
        put?: never;
        /** Accept an idempotent start, switch or stop */
        post: operations["submitCommand"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/commands/{command_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read accepted, applying or final command outcome */
        get: operations["getCommand"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read deployment-enabled connection inputs; saved secrets are never returned */
        get: operations["listCredentialInputs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials/operations/{operation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read durable connection operation progress and its confirmed outcome */
        get: operations["getCredentialOperation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials/operations/{operation_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resume the same blocked cleanup; never starts trading or skips unknown orders */
        post: operations["retryCredentialCleanup"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials/{exchange}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read saved versus loaded connection state without revealing credentials */
        get: operations["getExchangeCredentialStatus"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials/{exchange}/{connector}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Validate and save a full connection after acknowledged Stop; never resumes trading */
        post: operations["setExchangeCredentials"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials/{exchange}/{connector}/cleanup-credentials": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Replace an existing same-account key only to resume blocked cleanup */
        post: operations["repairCleanupCredentials"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/credentials/{exchange}/{connector}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stop trading, cancel bot orders, close managed perpetual positions, then remove this saved key */
        post: operations["disconnectExchangeCredentials"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/docs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Open the protected interactive API documentation */
        get: operations["getSwaggerUI"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/docs/assets/{asset}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read bundled Swagger UI assets */
        get: operations["getSwaggerAsset"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/fills": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read bounded fills, original fees and conversion coverage */
        get: operations["listFills"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/openapi.json": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read the generated API contract */
        get: operations["getOpenAPI"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/orders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read bounded bot-owned order history */
        get: operations["listOrders"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/orders/{order_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read one recorded order and its bounded status timeline */
        get: operations["getOrder"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/performance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read separate cached V2 controller performance; daily recorded fees/funding are linked */
        get: operations["getPerformance"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/positions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read last observed actual perpetual exposure, including after stop */
        get: operations["getPositions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read deployed MM defaults and limits */
        get: operations["listProfiles"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/reconciliation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read the latest stored check outcome without contacting OKX */
        get: operations["getReconciliation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/reconciliation/check": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Request one bounded observational check; does not repair or control trading */
        post: operations["requestReconciliation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/reconciliation/{check_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read the requested check even after a newer check starts */
        get: operations["getReconciliationById"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/recovery": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read recovery blockers and the latest reviewed operation */
        get: operations["getRecovery"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/recovery/check": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check stopped history and preview verified repairs; does not clear HALT */
        post: operations["checkRecovery"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/recovery/operations/{operation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read exact recovery progress or outcome without contacting the exchange */
        get: operations["getRecoveryOperation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/recovery/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revalidate and apply the reviewed technical recovery; leaves trading stopped */
        post: operations["resetRecovery"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/reference-pairs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search supported OKX spot USDT references, including pairs outside YAML */
        get: operations["listReferencePairs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Find recorded sessions; legacy sessions without creation history may be absent */
        get: operations["listSessions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/sessions/{session_id}/configuration": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read pinned session configuration; restart never replaces it with current YAML */
        get: operations["getSessionConfiguration"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/sessions/{session_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read recorded session transitions and original configuration */
        get: operations["getSessionEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/statistics/daily": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read market-day statistics with explicitly stale last-good fallback on transient refresh failure */
        get: operations["getDailyStatistics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/statistics/liquidity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read sampled own-quote coverage without exchange requests */
        get: operations["getLiquidity"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/admin/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read requested and observed bot state */
        get: operations["getRuntimeStatus"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AccountBalances */
        AccountBalancesOutput: {
            /** Account Id */
            account_id: string | null;
            /** Balances */
            balances: components["schemas"]["BalanceObservationOutput"][];
            /** Environment */
            environment: string;
            /** Identity Verified */
            identity_verified: boolean;
        };
        /** AccountResponse */
        AccountResponseOutput: {
            /** Active */
            active: boolean;
            /** Bound */
            bound: boolean;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Email */
            email: string;
            /** Id */
            id: string;
            /**
             * Role
             * @enum {string}
             */
            role: "admin" | "write" | "read";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /** AccountsPage */
        AccountsPageOutput: {
            /** Items */
            items: components["schemas"]["AccountResponseOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** ActionAvailability */
        ActionAvailabilityOutput: {
            /** Allowed */
            allowed: boolean;
            /** Detail */
            detail: string;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
        };
        /** AddAccountRequest */
        AddAccountRequest: {
            /**
             * Active
             * @default true
             */
            active: boolean;
            /** Email */
            email: string;
            /**
             * Role
             * @enum {string}
             */
            role: "admin" | "write" | "read";
        };
        /** AuditEventResponse */
        AuditEventResponseOutput: {
            /** Action */
            action: string;
            /** Actor Email */
            actor_email: string | null;
            /** Actor Id */
            actor_id: string | null;
            /** Actor Kind */
            actor_kind: string;
            /** Actor Role */
            actor_role: string | null;
            /** Connector */
            connector: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Details */
            details: {
                [key: string]: unknown;
            };
            /** Environment */
            environment: string | null;
            /** Exchange */
            exchange: string | null;
            /** Id */
            id: string;
            /** Operation Id */
            operation_id: string | null;
            /** Outcome */
            outcome: string;
            /** Reason Code */
            reason_code: string | null;
            /** Request Id */
            request_id: string;
            /** Target Id */
            target_id: string | null;
        };
        /** AuditPage */
        AuditPageOutput: {
            /** Items */
            items: components["schemas"]["AuditEventResponseOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** AutomaticRecoveryStatus */
        AutomaticRecoveryStatusOutput: {
            /**
             * Cold Recovery Supported
             * @default false
             */
            cold_recovery_supported: boolean;
            /**
             * Last Progress At
             * @default null
             */
            last_progress_at: number | null;
            /**
             * Next Retry At
             * @default null
             */
            next_retry_at: number | null;
            /**
             * Operator Required
             * @default false
             */
            operator_required: boolean;
            /**
             * Order Id
             * @default null
             */
            order_id: string | null;
            /**
             * Phase
             * @enum {string}
             */
            phase: "idle" | "reconciling" | "bootstrapping_recovery" | "waiting_for_readiness" | "settled_stopped" | "manual_required";
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
            /**
             * Stale
             * @default false
             */
            stale: boolean;
            /**
             * Started At
             * @default null
             */
            started_at: number | null;
        };
        /** BalanceObservation */
        BalanceObservationOutput: {
            /** Available Cash */
            available_cash: string | null;
            /** Available Equity */
            available_equity: string | null;
            /** Cash Balance */
            cash_balance: string | null;
            /** Connector */
            connector: string;
            /** Currency */
            currency: string;
            /** Equity */
            equity: string | null;
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /**
             * Present In Latest Snapshot
             * @default true
             */
            present_in_latest_snapshot: boolean;
            /**
             * Source
             * @constant
             */
            source: "okx_account_balance";
            /** Stale */
            stale: boolean;
        };
        /** BalancesResponse */
        BalancesResponseOutput: {
            /** Accounts */
            accounts: components["schemas"]["AccountBalancesOutput"][];
            /** Owner Available */
            owner_available: boolean;
            /**
             * Scope
             * @default account
             * @constant
             */
            scope: "account";
            /**
             * Server Time
             * Format: date-time
             */
            server_time: string;
        };
        /** BootstrapConfig */
        BootstrapConfigOutput: {
            /** Duration Seconds */
            duration_seconds: number;
            /** Max Anchor Move Bps */
            max_anchor_move_bps: string;
            /** Max Market Spread Bps */
            max_market_spread_bps: string;
            /** Max Quote */
            max_quote: string;
            /** Min External Depth Quote */
            min_external_depth_quote: string;
            /** Qualification Seconds */
            qualification_seconds: string;
        };
        /** BootstrapGuardStatus */
        BootstrapGuardStatusOutput: {
            /** Anchor */
            anchor: string;
            /** Expires At */
            expires_at: string | null;
            /** External Ask Quote */
            external_ask_quote: string;
            /** External Bid Quote */
            external_bid_quote: string;
            /** Lower Price */
            lower_price: string;
            /** Market Breakout Available */
            market_breakout_available: boolean;
            /** Market Qualified */
            market_qualified: boolean;
            /** Market Spread Bps */
            market_spread_bps: string | null;
            /** Max Quote */
            max_quote: string;
            /**
             * Price Source
             * @description Configured/model anchor is not independent market fair value.
             * @enum {string}
             */
            price_source: "configured_anchor" | "reference_model" | "market_book";
            /** Qualification Ready */
            qualification_ready: boolean;
            /** Qualification Started At */
            qualification_started_at: string | null;
            /** Qualification Valid Samples */
            qualification_valid_samples: number;
            /** Remaining Seconds */
            remaining_seconds: number | null;
            /** Started At */
            started_at: string | null;
            /**
             * State
             * @enum {string}
             */
            state: "bootstrap" | "market_guarded" | "waiting";
            /** Upper Price */
            upper_price: string;
        };
        /** BootstrapResponse */
        BootstrapResponseOutput: {
            /** Active Google Admins */
            active_google_admins: number;
            /** Basic Auth Enabled */
            basic_auth_enabled: boolean;
            /** Detail */
            detail: string;
            /** Google Admin Ready */
            google_admin_ready: boolean;
            /** Google Auth Enabled */
            google_auth_enabled: boolean;
        };
        /** ChangeAccountRequest */
        ChangeAccountRequest: {
            /**
             * Active
             * @default null
             */
            active: boolean | null;
            /** Expected Version */
            expected_version: number;
            /**
             * Role
             * @default null
             */
            role: ("admin" | "write" | "read") | null;
        };
        /**
         * CloseType
         * @enum {integer}
         */
        CloseTypeOutput: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10;
        /** CommandResponse */
        CommandResponseOutput: {
            /**
             * Action
             * @enum {string}
             */
            action: "start" | "switch" | "stop";
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Effective Version */
            effective_version: number;
            /**
             * Error
             * @default null
             */
            error: string | null;
            /** Expected Version */
            expected_version: number;
            /** Id */
            id: string;
            /**
             * Payload
             * @description Original receipt, including historical overrides when present. These legacy fields are not accepted on new commands.
             */
            payload: components["schemas"]["RecordedStartCommandOutput"] | components["schemas"]["RecordedSwitchCommandOutput"] | components["schemas"]["StopCommandOutput"];
            /** Session Id */
            session_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "accepted" | "applying" | "succeeded" | "failed" | "superseded";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** CommandsPage */
        CommandsPageOutput: {
            /** Items */
            items: components["schemas"]["CommandResponseOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** ConfigurationPolicy */
        ConfigurationPolicyOutput: {
            /**
             * Bound Policies Require Review
             * @default true
             */
            bound_policies_require_review: boolean;
            /**
             * Profile Settings
             * @default yaml_only
             * @constant
             */
            profile_settings: "yaml_only";
            /**
             * Random Settings Source
             * @default yaml
             * @constant
             */
            random_settings_source: "yaml";
            /**
             * Restart Behavior
             * @default restore_persisted_session
             * @constant
             */
            restart_behavior: "restore_persisted_session";
            /** Session Controls */
            session_controls?: string[];
            /**
             * Yaml Change Behavior
             * @default new_sessions_only
             * @constant
             */
            yaml_change_behavior: "new_sessions_only";
        };
        /** CredentialAction */
        CredentialActionOutput: {
            /** Allowed */
            allowed: boolean;
            /** Detail */
            detail: string;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
        };
        /** CredentialCatalogue */
        CredentialCatalogueOutput: {
            /** Exchanges */
            exchanges: components["schemas"]["ExchangeCredentialDescriptorOutput"][];
            /**
             * Source
             * @enum {string}
             */
            source: "environment" | "postgres_kms";
        };
        /** CredentialCleanupKeyRequest */
        CredentialCleanupKeyRequest: {
            /** Credentials */
            credentials: {
                [key: string]: string;
            };
            /** Expected Control Version */
            expected_control_version: number;
            /** Expected Operation Version */
            expected_operation_version: number;
            /** Expected Version */
            expected_version: number;
            /** Operation Id */
            operation_id: string;
        };
        /** CredentialDisconnectRequest */
        CredentialDisconnectRequest: {
            /** Expected Control Version */
            expected_control_version: number;
            /** Expected Version */
            expected_version: number;
        };
        /** CredentialInputField */
        CredentialInputFieldOutput: {
            /** Label */
            label: string;
            /** Name */
            name: string;
            /** Required */
            required: boolean;
            /** Secret */
            secret: boolean;
        };
        /** CredentialPayload */
        CredentialPayloadOutput: {
            /**
             * Action
             * @enum {string}
             */
            action: "credentials_set" | "credentials_disconnect" | "credentials_cleanup_key" | "credentials_cleanup_retry";
            /** Actor Id */
            actor_id: string;
            /** Actor Kind */
            actor_kind: string;
            /** Bot Id */
            bot_id: string;
            /**
             * Cleanup Operation Id
             * @default null
             */
            cleanup_operation_id: string | null;
            /** Connector */
            connector: string;
            /** Environment */
            environment: string;
            /** Exchange */
            exchange: string;
            /** Expected Control Version */
            expected_control_version: number;
            /**
             * Expected Operation Version
             * @default null
             */
            expected_operation_version: number | null;
            /** Expected Version */
            expected_version: number;
        };
        /** CredentialProgress */
        CredentialProgressOutput: {
            /**
             * Account Verified
             * @default null
             */
            account_verified: boolean | null;
            /**
             * Blocked Stage
             * @default null
             */
            blocked_stage: string | null;
            /**
             * Cleanup Operation Id
             * @default null
             */
            cleanup_operation_id: string | null;
            /**
             * Consumers Detached
             * @default null
             */
            consumers_detached: boolean | null;
            /** Credential Version */
            credential_version: number;
            /**
             * Ledger Confirmed
             * @default null
             */
            ledger_confirmed: boolean | null;
            /**
             * Next Retry At
             * @default null
             */
            next_retry_at: string | null;
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /**
             * Operation Version
             * @default 0
             */
            operation_version: number;
            /**
             * Operator Action Required
             * @default false
             */
            operator_action_required: boolean;
            /**
             * Orders Terminal Verified
             * @default null
             */
            orders_terminal_verified: boolean | null;
            /**
             * Owner Generation
             * @default null
             */
            owner_generation: number | null;
            /**
             * Owner Uuid
             * @default null
             */
            owner_uuid: string | null;
            /**
             * Position Checkpoints Verified
             * @default null
             */
            position_checkpoints_verified: boolean | null;
            /**
             * Positions Flat Verified
             * @default null
             */
            positions_flat_verified: boolean | null;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
            /**
             * Remaining Order Count
             * @default null
             */
            remaining_order_count: number | null;
            /**
             * Remaining Position Count
             * @default null
             */
            remaining_position_count: number | null;
            /** Stage */
            stage: string;
        };
        /** CredentialReceipt */
        CredentialReceiptOutput: {
            /**
             * Action
             * @enum {string}
             */
            action: "credentials_set" | "credentials_disconnect" | "credentials_cleanup_key" | "credentials_cleanup_retry";
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Effective Version */
            effective_version: number;
            /**
             * Error
             * @default null
             */
            error: string | null;
            /** Expected Version */
            expected_version: number;
            /** Id */
            id: string;
            payload: components["schemas"]["CredentialPayloadOutput"];
            result: components["schemas"]["CredentialProgressOutput"];
            /**
             * Session Id
             * @default null
             */
            session_id: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "accepted" | "applying" | "succeeded" | "failed";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** CredentialRetryRequest */
        CredentialRetryRequest: {
            /** Expected Control Version */
            expected_control_version: number;
            /** Expected Operation Version */
            expected_operation_version: number;
            /** Expected Version */
            expected_version: number;
        };
        /** CredentialSetRequest */
        CredentialSetRequest: {
            /** Credentials */
            credentials: {
                [key: string]: string;
            };
            /** Expected Control Version */
            expected_control_version: number;
            /** Expected Version */
            expected_version: number;
        };
        /** CredentialSlot */
        CredentialSlotOutput: {
            /** Actions */
            actions: {
                [key: string]: components["schemas"]["CredentialActionOutput"];
            };
            /**
             * Changed At
             * @default null
             */
            changed_at: string | null;
            /**
             * Changed By
             * @default null
             */
            changed_by: string | null;
            /**
             * Cleanup Operation Id
             * @default null
             */
            cleanup_operation_id: string | null;
            /** Configured */
            configured: boolean;
            /** Connector */
            connector: string;
            /** Detail */
            detail: string;
            /** Label */
            label: string;
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /**
             * Operation Id
             * @default null
             */
            operation_id: string | null;
            /**
             * Operation Version
             * @default null
             */
            operation_version: number | null;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
            /** Status */
            status: string;
            /** Version */
            version: number;
        };
        /** DailyBalance */
        DailyBalanceOutput: {
            /** Account Id */
            account_id: string;
            /** Available Cash */
            available_cash: string | null;
            /** Available Equity */
            available_equity: string | null;
            /** Cash Balance */
            cash_balance: string | null;
            /** Connector */
            connector: string;
            /** Currency */
            currency: string;
            /**
             * Day
             * Format: date
             */
            day: string;
            /** Environment */
            environment: string;
            /** Equity */
            equity: string | null;
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Reporting Timezone */
            reporting_timezone: string;
            /** Source */
            source: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** DailyBucket */
        DailyBucketOutput: {
            /** Buy Volume Base */
            buy_volume_base: string;
            /** Buy Volume Quote */
            buy_volume_quote: string;
            /** Converted Fill Count */
            converted_fill_count: number;
            /**
             * Day
             * Format: date
             */
            day: string;
            /** Fee In Usdt */
            fee_in_usdt: string | null;
            /** Fees */
            fees: components["schemas"]["DailyFeeOutput"][];
            /** Fill Count */
            fill_count: number;
            /**
             * Funding Paid
             * @description Observed settlement payments; null when no settlement basis is recorded. Consult funding_sync coverage.
             */
            funding_paid: string | null;
            /**
             * Funding Received
             * @description Observed settlement receipts; null when no settlement basis is recorded. Consult funding_sync coverage.
             */
            funding_received: string | null;
            /** Last Event At */
            last_event_at: string | null;
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Percentage Fee Fill Count */
            percentage_fee_fill_count: number;
            /** Sell Volume Base */
            sell_volume_base: string;
            /** Sell Volume Quote */
            sell_volume_quote: string;
            /**
             * Source
             * @default recorder_tables
             * @constant
             */
            source: "recorder_tables";
            /** Unconverted Fill Count */
            unconverted_fill_count: number;
        };
        /** DailyFee */
        DailyFeeOutput: {
            /** Currency */
            currency: string;
            /** Net */
            net: string;
            /** Paid */
            paid: string;
            /** Rebates */
            rebates: string;
        };
        /** DailyMarket */
        DailyMarketOutput: {
            /** Connector */
            connector: string;
            /** Days */
            days: components["schemas"]["DailyBucketOutput"][];
            /**
             * Funding Status
             * @enum {string}
             */
            funding_status: "not_applicable" | "observed_only";
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
        };
        /** DailyResponse */
        DailyResponseOutput: {
            /**
             * Balance Scope
             * @constant
             */
            balance_scope: "account_last_observed";
            /** Balances */
            balances: components["schemas"]["DailyBalanceOutput"][];
            /**
             * Coverage
             * @constant
             */
            coverage: "observed_recorder_events";
            /**
             * End Day
             * Format: date
             */
            end_day: string;
            /**
             * Freshness
             * @description Fresh database/cached observation or explicitly degraded last-good fallback. server_time remains the original database observation time.
             * @default fresh
             * @enum {string}
             */
            freshness: "fresh" | "stale";
            /**
             * Funding Precision
             * @default decimal_bills_with_legacy_float
             * @constant
             */
            funding_precision: "decimal_bills_with_legacy_float";
            /** @default null */
            funding_sync: components["schemas"]["FundingSyncStatusOutput"] | null;
            /** Markets */
            markets: components["schemas"]["DailyMarketOutput"][];
            /**
             * Message
             * @default null
             */
            message: string | null;
            /**
             * Observation Age Seconds
             * @description Age of a stale fallback; never more than 60 seconds from the original observation.
             * @default null
             */
            observation_age_seconds: number | null;
            /** Reporting Timezone */
            reporting_timezone: string;
            /**
             * Retry After Seconds
             * @description Suggested refresh delay for a stale fallback.
             * @default null
             */
            retry_after_seconds: number | null;
            /**
             * Server Time
             * Format: date-time
             */
            server_time: string;
            /**
             * Start Day
             * Format: date
             */
            start_day: string;
            /**
             * Trade Storage Scale
             * @description PostgreSQL NUMERIC has no fixed storage scale.
             * @default null
             */
            trade_storage_scale: number | null;
        };
        /** DrawdownControllerStatus */
        DrawdownControllerStatusOutput: {
            /** Drawdown Quote */
            drawdown_quote: string | null;
            /** Peak Quote */
            peak_quote: string | null;
            /** Pnl Quote */
            pnl_quote: string | null;
        };
        /** DrawdownStatus */
        DrawdownStatusOutput: {
            /**
             * Breached Controller Id
             * @default null
             */
            breached_controller_id: string | null;
            /** Controllers */
            controllers: {
                [key: string]: components["schemas"]["DrawdownControllerStatusOutput"];
            };
            /** Enabled */
            enabled: boolean;
            /**
             * Fees
             * @constant
             */
            fees: "executor_and_held_trade_quote_fees";
            /**
             * Funding
             * @constant
             */
            funding: "excluded";
            /**
             * Global Drawdown Quote
             * @default null
             */
            global_drawdown_quote: string | null;
            /**
             * Global Peak Quote
             * @default null
             */
            global_peak_quote: string | null;
            /**
             * Global Pnl Quote
             * @default null
             */
            global_pnl_quote: string | null;
            /** Max Controller Drawdown Quote */
            max_controller_drawdown_quote: string | null;
            /** Max Global Drawdown Quote */
            max_global_drawdown_quote: string | null;
            /**
             * Metric
             * @description Covered bot V2 report peak drawdown; not account equity or campaign profit.
             * @constant
             */
            metric: "covered_v2_report_drawdown";
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /** Operator Action Required */
            operator_action_required: boolean;
            /** Ready */
            ready: boolean;
            /** Reason Code */
            reason_code: string | null;
            /** Scope */
            scope: {
                [key: string]: components["schemas"]["MMDrawdownMarketOutput"];
            };
            /** Stale */
            stale: boolean;
        };
        /** ErrorResponse */
        ErrorResponse: {
            /**
             * Code
             * @default null
             */
            code: string | null;
            /**
             * Detail
             * @default null
             */
            detail: string | null;
            /** Error */
            error: string;
            /** Fields */
            fields?: components["schemas"]["FieldError"][];
            /**
             * Request Id
             * @default null
             */
            request_id: string | null;
            /**
             * Retry
             * @default null
             */
            retry: ("fix_request" | "refresh_state" | "same_request" | "wait" | "contact_support" | "reconnect") | null;
            /**
             * Retry After Seconds
             * @default null
             */
            retry_after_seconds: number | null;
            /** @default null */
            service: components["schemas"]["ServiceStatus"] | null;
        };
        /** ExchangeCredentialDescriptor */
        ExchangeCredentialDescriptorOutput: {
            /** Cleanup Mode */
            cleanup_mode: string;
            /** Connectors */
            connectors: string[];
            /**
             * Disconnect Available
             * @default false
             */
            disconnect_available: boolean;
            /** Environment */
            environment: string;
            /** Exchange */
            exchange: string;
            /** Fields */
            fields: components["schemas"]["CredentialInputFieldOutput"][];
            /**
             * Site
             * @default null
             */
            site: string | null;
        };
        /** ExchangeCredentialStatus */
        ExchangeCredentialStatusOutput: {
            /** Environment */
            environment: string;
            /** Exchange */
            exchange: string;
            /** Slots */
            slots: components["schemas"]["CredentialSlotOutput"][];
            /**
             * Source
             * @enum {string}
             */
            source: "environment" | "postgres_kms";
        };
        /** FeeAmount */
        FeeAmountOutput: {
            /** Amount */
            amount: string;
            /** Token */
            token: string;
        };
        /** FieldError */
        FieldError: {
            /** Code */
            code: string;
            /** Path */
            path: string;
        };
        /** FillView */
        FillViewOutput: {
            /** Amount */
            amount: string;
            /** Connector */
            connector: string;
            /**
             * Conversion Status
             * @enum {string}
             */
            conversion_status: "recorded" | "unavailable" | "legacy_ambiguous";
            fee: components["schemas"]["RecordedFeeOutput"];
            /** Fee In Quote */
            fee_in_quote: string | null;
            /** Id */
            id: string;
            /** Leverage */
            leverage: number;
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /** Order Id */
            order_id: string;
            /** Pair */
            pair: string;
            /** Position */
            position: string | null;
            /** Price */
            price: string;
            /** Quote Currency */
            quote_currency: string;
            /**
             * Side
             * @enum {string}
             */
            side: "BUY" | "SELL";
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
        };
        /** FillsPage */
        FillsPageOutput: {
            /** Items */
            items: components["schemas"]["FillViewOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** FundingSyncStatus */
        FundingSyncStatusOutput: {
            /**
             * Account Id
             * @default null
             */
            account_id: string | null;
            /**
             * Backfill Pending
             * @default false
             */
            backfill_pending: boolean;
            /**
             * Covered From Ms
             * @description Start of the verified bill-read interval; UTC epoch milliseconds.
             * @default null
             */
            covered_from_ms: number | null;
            /**
             * Covered Until Ms
             * @description Exclusive end of the committed interval; not lifetime-history coverage.
             * @default null
             */
            covered_until_ms: number | null;
            /**
             * Environment
             * @default null
             */
            environment: ("live" | "demo") | null;
            /**
             * Error
             * @default null
             */
            error: string | null;
            /**
             * History Gap From Ms
             * @default null
             */
            history_gap_from_ms: number | null;
            /**
             * History Gap Until Ms
             * @default null
             */
            history_gap_until_ms: number | null;
            /**
             * Last Success Ms
             * @default null
             */
            last_success_ms: number | null;
            /**
             * Legacy History Unverified
             * @default null
             */
            legacy_history_unverified: boolean | null;
            /** Pairs */
            pairs?: string[];
            /**
             * Source
             * @default okx_account_bills
             * @constant
             */
            source: "okx_account_bills";
            /**
             * Stale
             * @default true
             */
            stale: boolean;
            /**
             * State
             * @enum {string}
             */
            state: "not_configured" | "waiting" | "backfilling" | "observed" | "unavailable";
        };
        /**
         * InitialPositionConfig
         * @description Configuration for an initial position that the controller should consider.
         *     This is used when the user already has assets in their account and wants
         *     the controller to manage them.
         */
        InitialPositionConfigOutput: {
            /** Amount */
            amount: string;
            /** Connector Name */
            connector_name: string;
            side: components["schemas"]["TradeTypeOutput"];
            /** Trading Pair */
            trading_pair: string;
        };
        /** InventorySkewConfig */
        InventorySkewConfigOutput: {
            /**
             * Enabled
             * @default false
             */
            enabled: boolean;
            /**
             * Inventory Range Multiplier
             * @default 1
             */
            inventory_range_multiplier: string;
            /**
             * Target Base Pct
             * @default 0.5
             */
            target_base_pct: string;
        };
        /** InventorySkewStatus */
        InventorySkewStatusOutput: {
            /** Balance Observed At */
            balance_observed_at: string | null;
            /** Base Cash */
            base_cash: string | null;
            /** Base Pct */
            base_pct: string | null;
            /** Budget Scale */
            budget_scale: string;
            /** Buy Multiplier */
            buy_multiplier: string;
            /** Cash Quote */
            cash_quote: string | null;
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Reason Code */
            reason_code: string | null;
            /**
             * Scope
             * @description Observed spot cash inventory; not an isolated campaign wallet.
             * @constant
             */
            scope: "account_cash";
            /** Sell Multiplier */
            sell_multiplier: string;
            /**
             * State
             * @enum {string}
             */
            state: "active" | "neutral";
            /** Valuation Price */
            valuation_price: string | null;
        };
        /** LifeMMConfig */
        LifeMMConfigOutput: {
            /**
             * Buy Amounts Pct
             * @default null
             */
            buy_amounts_pct: string[] | null;
            /**
             * Buy Spreads
             * @default 0.01,0.02
             */
            buy_spreads: number[];
            /**
             * Connector Name
             * @default okx
             * @enum {string}
             */
            connector_name: "okx" | "okx_perpetual";
            /**
             * Controller Name
             * @default life_mm
             */
            controller_name: string;
            /**
             * Controller Type
             * @default market_making
             */
            controller_type: string;
            /**
             * Cooldown Time
             * @default 15
             */
            cooldown_time: number;
            /**
             * Executor Refresh Time
             * @default 300
             */
            executor_refresh_time: number;
            /**
             * Id
             * @description Unique identifier for the controller. Required.
             */
            id: string;
            /**
             * Initial Anchor
             * @default null
             */
            initial_anchor: string | null;
            /**
             * Initial Positions
             * @default []
             */
            initial_positions: components["schemas"]["InitialPositionConfigOutput"][];
            inventory_skew?: components["schemas"]["InventorySkewConfigOutput"];
            /**
             * Leverage
             * @default 1
             */
            leverage: number;
            /**
             * Manual Kill Switch
             * @default false
             */
            manual_kill_switch: boolean;
            /**
             * Margin Mode
             * @default CROSS
             * @constant
             */
            margin_mode: "CROSS";
            /**
             * Max Base Inventory
             * @default null
             */
            max_base_inventory: string | null;
            /**
             * Max Data Age Seconds
             * @default 10
             */
            max_data_age_seconds: string;
            /**
             * Max Gross Base
             * @default null
             */
            max_gross_base: string | null;
            /**
             * Max Long Base
             * @default null
             */
            max_long_base: string | null;
            /** Max Price */
            max_price: string;
            /**
             * Max Reference Pairs
             * @default 8
             */
            max_reference_pairs: number;
            /**
             * Max Short Base
             * @default null
             */
            max_short_base: string | null;
            /** Min Price */
            min_price: string;
            /**
             * Mm Generation
             * @default null
             */
            mm_generation: number | null;
            /**
             * Mm Session Id
             * @default null
             */
            mm_session_id: string | null;
            /** @default HEDGE */
            position_mode: components["schemas"]["PositionModeOutput"];
            /**
             * Position Rebalance Threshold Pct
             * @default 0.05
             */
            position_rebalance_threshold_pct: string;
            quote_guards?: components["schemas"]["QuoteGuardConfigOutput"];
            /**
             * Quote Refresh Max Delay Seconds
             * @description Optional bounded delay for rolling ordinary refresh; zero preserves batch refresh. Safety withdrawals are immediate.
             * @default 0
             */
            quote_refresh_max_delay_seconds: number;
            /**
             * Reference Connector
             * @default okx
             * @constant
             */
            reference_connector: "okx";
            /** References */
            references?: components["schemas"]["ReferencePairOutput"][];
            /**
             * Runtime Config Managed
             * @default false
             */
            runtime_config_managed: boolean;
            /**
             * Saved Life Anchor
             * @default null
             */
            saved_life_anchor: string | null;
            /** Saved Source Anchors */
            saved_source_anchors?: {
                [key: string]: string;
            };
            /**
             * Scenario
             * @default sideways
             * @enum {string}
             */
            scenario: "follow_market" | "sideways" | "follow_token";
            /**
             * Sell Amounts Pct
             * @default null
             */
            sell_amounts_pct: string[] | null;
            /**
             * Sell Spreads
             * @default 0.01,0.02
             */
            sell_spreads: number[];
            /** @default null */
            sideways_range: components["schemas"]["SidewaysRangeOutput"] | null;
            /**
             * Skip Rebalance
             * @default true
             */
            skip_rebalance: boolean;
            spread_policy?: components["schemas"]["SpreadPolicyConfigOutput"];
            /**
             * Stop Loss
             * @default null
             */
            stop_loss: string | null;
            /**
             * Take Profit
             * @default null
             */
            take_profit: string | null;
            /** @default 2 */
            take_profit_order_type: components["schemas"]["OrderTypeOutput"];
            /**
             * Time Limit
             * @default null
             */
            time_limit: number | null;
            /**
             * Total Amount Quote
             * @default 100
             */
            total_amount_quote: string;
            /**
             * Trading Pair
             * @default LIFE-USDT
             */
            trading_pair: string;
            /** @default null */
            trailing_stop: components["schemas"]["TrailingStopOutput"] | null;
        };
        /** LiquidityMarket */
        LiquidityMarketOutput: {
            /** Ask Notional */
            ask_notional: string | null;
            /** Best Ask */
            best_ask: string | null;
            /** Best Bid */
            best_bid: string | null;
            /** Bid Notional */
            bid_notional: string | null;
            /**
             * Buy Order Count
             * @default null
             */
            buy_order_count: number | null;
            /** Connector */
            connector: string | null;
            /** Elapsed Seconds */
            elapsed_seconds: number;
            /** Eligible */
            eligible: boolean;
            /** Eligible Seconds */
            eligible_seconds: number;
            /** Observed Seconds */
            observed_seconds: number;
            /** Pair */
            pair: string | null;
            /** @default null */
            quote_activity: components["schemas"]["QuoteActivityOutput"] | null;
            /** Reason Code */
            reason_code: string | null;
            /**
             * Sell Order Count
             * @default null
             */
            sell_order_count: number | null;
            /** Spread Bps */
            spread_bps: string | null;
            /** Two Sided */
            two_sided: boolean;
            /** Two Sided Fraction Of Elapsed */
            two_sided_fraction_of_elapsed: number | null;
            /** Two Sided Seconds */
            two_sided_seconds: number;
            /** Unknown Seconds */
            unknown_seconds: number;
            /** Valid */
            valid: boolean;
            /**
             * Working Orders With Fills
             * @default null
             */
            working_orders_with_fills: number | null;
        };
        /** LiquidityResponse */
        LiquidityResponseOutput: {
            /**
             * Coverage Started At
             * @default null
             */
            coverage_started_at: string | null;
            /**
             * Ended At
             * @default null
             */
            ended_at: string | null;
            /**
             * Generation
             * @default null
             */
            generation: number | null;
            /** Markets */
            markets: {
                [key: string]: components["schemas"]["LiquidityMarketOutput"];
            };
            /**
             * Measurement
             * @constant
             */
            measurement: "sampled_owner_observations";
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /**
             * Profile Id
             * @default null
             */
            profile_id: string | null;
            /** Session Id */
            session_id: string | null;
            /**
             * Started At
             * @default null
             */
            started_at: string | null;
            /**
             * State
             * @enum {string}
             */
            state: "observed" | "unavailable";
        };
        /** MMDrawdownMarket */
        MMDrawdownMarketOutput: {
            /**
             * Connector Name
             * @enum {string}
             */
            connector_name: "okx" | "okx_perpetual";
            /** Trading Pair */
            trading_pair: string;
        };
        /** MMEventView */
        MMEventViewOutput: {
            /** Bot Id */
            bot_id: string;
            /** Control Version */
            control_version: number;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Event Key */
            event_key: string;
            /** Event Type */
            event_type: string;
            /** Generation */
            generation: number;
            /** Id */
            id: number;
            /** New Profile */
            new_profile: string | null;
            /** Old Profile */
            old_profile: string | null;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Principal */
            principal: string | null;
            /** Session Id */
            session_id: string;
            /** Source */
            source: string;
        };
        /** MMEventsPage */
        MMEventsPageOutput: {
            /**
             * Coverage
             * @constant
             */
            coverage: "recorded_transitions_since_migration";
            /**
             * History Complete
             * @description A recorded session-start configuration exists; guard changes are sampled observations.
             */
            history_complete: boolean;
            /** Items */
            items: components["schemas"]["MMEventViewOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
            /** Session Id */
            session_id: string;
        };
        /** MMProfile */
        MMProfileOutput: {
            /**
             * Anchor
             * @default session_start_mid
             * @constant
             */
            anchor: "session_start_mid";
            /** References */
            references?: components["schemas"]["ReferencePairOutput"][];
            /**
             * Scenario
             * @enum {string}
             */
            scenario: "follow_market" | "sideways" | "follow_token";
            /** @default null */
            sideways_range: components["schemas"]["SidewaysRangeOutput"] | null;
        };
        /** MMProfilesConfig */
        MMProfilesConfigOutput: {
            /**
             * Allow Until Stopped
             * @description Allow explicitly requested sessions with no overall expiry; risk policies and random-step deadlines remain active.
             * @default false
             */
            allow_until_stopped: boolean;
            /**
             * Default Duration Seconds
             * @default 3600
             */
            default_duration_seconds: number;
            /** Markets */
            markets: {
                [key: string]: components["schemas"]["LifeMMConfigOutput"];
            };
            /**
             * Max Controller Drawdown Quote
             * @default null
             */
            max_controller_drawdown_quote: string | null;
            /**
             * Max Duration Seconds
             * @default 86400
             */
            max_duration_seconds: number;
            /**
             * Max Global Drawdown Quote
             * @default null
             */
            max_global_drawdown_quote: string | null;
            /** Max Total Amount Quote */
            max_total_amount_quote: string;
            /**
             * Min Duration Seconds
             * @default 60
             */
            min_duration_seconds: number;
            /** Profiles */
            profiles: {
                [key: string]: components["schemas"]["MMProfileOutput"];
            };
            random: components["schemas"]["RandomSelectionOutput"];
            /**
             * Reference Connector
             * @default okx
             * @constant
             */
            reference_connector: "okx";
            /** @default null */
            spot_outflow: components["schemas"]["SpotOutflowConfigOutput"] | null;
        };
        /** MMSessionRequest */
        MMSessionRequestOutput: {
            /**
             * Duration Mode
             * @default timed
             * @enum {string}
             */
            duration_mode: "timed" | "until_stopped";
            /**
             * Duration Seconds
             * @default null
             */
            duration_seconds: number | null;
            /**
             * Market Mode
             * @enum {string}
             */
            market_mode: "spot" | "perpetual" | "both";
            /**
             * Profile Id
             * @default null
             */
            profile_id: string | null;
            /**
             * Profile Ids
             * @default null
             */
            profile_ids: string[] | null;
            /**
             * References
             * @default null
             */
            references: components["schemas"]["ReferencePairOutput"][] | null;
            /**
             * Selection
             * @default fixed
             * @enum {string}
             */
            selection: "fixed" | "random";
            /**
             * Step Duration Seconds
             * @default null
             */
            step_duration_seconds: number | null;
        };
        /** MarketReadiness */
        MarketReadinessOutput: {
            /** Admitted */
            admitted: boolean;
            /** Connector */
            connector: string;
            /** @default null */
            inventory_skew: components["schemas"]["InventorySkewStatusOutput"] | null;
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /** Pair */
            pair: string;
            /** @default null */
            quote_guard: components["schemas"]["QuoteGuardStatusOutput"] | null;
            /** Ready */
            ready: boolean;
            /** Reason */
            reason: string | null;
            /** Reference Price */
            reference_price: string | null;
            /** @default null */
            spread_policy: components["schemas"]["SpreadPolicyStatusOutput"] | null;
        };
        /** ObservationStatus */
        ObservationStatusOutput: {
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
            /** Session Matches */
            session_matches: boolean;
            /** Stale */
            stale: boolean;
        };
        /** OperatorStatus */
        OperatorStatusOutput: {
            /** Detail */
            detail: string;
            /** Label */
            label: string;
            /**
             * Next Action
             * @enum {string}
             */
            next_action: "wait" | "contact_support" | "start" | "monitor" | "check_recovery";
            /**
             * State
             * @enum {string}
             */
            state: "unavailable" | "needs_attention" | "stopped" | "preparing" | "switching" | "stopping" | "waiting" | "running";
        };
        /** OrderDetail */
        OrderDetailOutput: {
            /** Fills Url */
            fills_url: string;
            order: components["schemas"]["OrderViewOutput"];
            /** Timeline */
            timeline: components["schemas"]["OrderTimelineOutput"][];
            /** Timeline Truncated */
            timeline_truncated: boolean;
        };
        /** OrderTimeline */
        OrderTimelineOutput: {
            /** Id */
            id: number;
            /** Status */
            status: string;
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
        };
        /**
         * OrderType
         * @enum {integer}
         */
        OrderTypeOutput: 1 | 2 | 3 | 4 | 5 | 6;
        /** OrderView */
        OrderViewOutput: {
            /** Amount */
            amount: string;
            /** Connector */
            connector: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Creation Pending */
            creation_pending: boolean;
            /** Exchange Order Id */
            exchange_order_id: string | null;
            /** Id */
            id: string;
            /** Leverage */
            leverage: number;
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /** Order Type */
            order_type: string;
            /** Pair */
            pair: string;
            /** Position */
            position: string | null;
            /** Price */
            price: string;
            /**
             * Progress Basis
             * @default null
             */
            progress_basis: ("recorded_fills_only" | "provisional_order" | "inconsistent_recording") | null;
            /**
             * Recorded Filled Amount
             * @default null
             */
            recorded_filled_amount: string | null;
            /**
             * Recorded Remaining Amount
             * @description Original amount minus recorded fills, not exchange resting quantity. Can remain positive after cancellation; null for provisional/inconsistent orders.
             * @default null
             */
            recorded_remaining_amount: string | null;
            /** Side */
            side: ("BUY" | "SELL") | null;
            /** Status */
            status: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** OrdersPage */
        OrdersPageOutput: {
            /** Items */
            items: components["schemas"]["OrderViewOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** PerformanceMarket */
        PerformanceMarketOutput: {
            /**
             * Coverage
             * @enum {string}
             */
            coverage: "observed" | "unavailable";
            /** Daily Statistics Url */
            daily_statistics_url: string;
            /**
             * Funding Status
             * @enum {string}
             */
            funding_status: "not_applicable" | "observed_only";
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /** Observations */
            observations: components["schemas"]["PerformanceObservationOutput"][];
        };
        /** PerformanceObservation */
        PerformanceObservationOutput: {
            /** Connector */
            connector: string;
            /** Controller Id */
            controller_id: string;
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Pair */
            pair: string;
            report: components["schemas"]["PerformanceReportOutput"];
            /**
             * Source
             * @constant
             */
            source: "v2_controller_report_cache";
            /** Stale */
            stale: boolean;
            /**
             * Valuation Basis
             * @constant
             */
            valuation_basis: "executor_and_position_hold_reports";
        };
        /** PerformanceReport */
        PerformanceReportOutput: {
            /**
             * Close Type Counts
             * @default {}
             */
            close_type_counts: {
                [key: string]: number;
            };
            /**
             * Global Pnl Pct
             * @default 0
             */
            global_pnl_pct: string;
            /**
             * Global Pnl Quote
             * @default 0
             */
            global_pnl_quote: string;
            /**
             * Positions Summary
             * @default []
             */
            positions_summary: unknown[];
            /**
             * Realized Pnl Pct
             * @default 0
             */
            realized_pnl_pct: string;
            /**
             * Realized Pnl Quote
             * @default 0
             */
            realized_pnl_quote: string;
            /**
             * Unrealized Pnl Pct
             * @default 0
             */
            unrealized_pnl_pct: string;
            /**
             * Unrealized Pnl Quote
             * @default 0
             */
            unrealized_pnl_quote: string;
            /**
             * Volume Traded
             * @default 0
             */
            volume_traded: string;
        };
        /** PerformanceResponse */
        PerformanceResponseOutput: {
            /** Markets */
            markets: components["schemas"]["PerformanceMarketOutput"][];
            /** Owner Available */
            owner_available: boolean;
            /**
             * Scope
             * @default controller_lifetime_and_active_exposure
             * @constant
             */
            scope: "controller_lifetime_and_active_exposure";
            /**
             * Server Time
             * Format: date-time
             */
            server_time: string;
        };
        /** PositionMarket */
        PositionMarketOutput: {
            /** Connector */
            connector: string;
            /**
             * Coverage
             * @enum {string}
             */
            coverage: "observed" | "unavailable" | "not_applicable";
            /** Items */
            items?: components["schemas"]["PositionObservationOutput"][];
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /** Pair Observations */
            pair_observations?: {
                [key: string]: string;
            };
            /**
             * Source
             * @default okx_account_positions
             * @constant
             */
            source: "okx_account_positions";
            /**
             * Stale
             * @default true
             */
            stale: boolean;
        };
        /**
         * PositionMode
         * @enum {string}
         */
        PositionModeOutput: "HEDGE" | "ONEWAY";
        /** PositionObservation */
        PositionObservationOutput: {
            /** Amount */
            amount: string;
            /** Entry Price */
            entry_price: string;
            /** Leverage */
            leverage: string;
            /**
             * Liquidation Price
             * @default null
             */
            liquidation_price: string | null;
            /**
             * Margin
             * @default null
             */
            margin: string | null;
            /**
             * Margin Mode
             * @default null
             */
            margin_mode: string | null;
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /** Pair */
            pair: string;
            /**
             * Side
             * @enum {string}
             */
            side: "LONG" | "SHORT";
            /** Stale */
            stale: boolean;
            /** Unrealized Pnl */
            unrealized_pnl: string;
        };
        /** PositionsResponse */
        PositionsResponseOutput: {
            /** Markets */
            markets: components["schemas"]["PositionMarketOutput"][];
            /** Owner Available */
            owner_available: boolean;
            /**
             * Server Time
             * Format: date-time
             */
            server_time: string;
        };
        /** PrincipalResponse */
        PrincipalResponseOutput: {
            /**
             * Email
             * @default null
             */
            email: string | null;
            /** Id */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "basic" | "google";
            /** Permissions */
            permissions: {
                [key: string]: boolean;
            };
            /**
             * Role
             * @enum {string}
             */
            role: "admin" | "write" | "read";
            /** Role Label */
            role_label: string;
        };
        /** ProfilesResponse */
        ProfilesResponseOutput: {
            catalogue: components["schemas"]["MMProfilesConfigOutput"];
            /**
             * Catalogue Hash
             * @default null
             */
            catalogue_hash: string | null;
            configuration_policy?: components["schemas"]["ConfigurationPolicyOutput"];
            /** Duration Modes */
            duration_modes?: ("timed" | "until_stopped")[];
            /** Market Modes */
            market_modes: ("spot" | "perpetual" | "both")[];
            /** @default null */
            process_identity: components["schemas"]["RuntimeIdentityOutput"] | null;
            /** Reporting Timezone */
            reporting_timezone: string;
        };
        /** QuoteActivity */
        QuoteActivityOutput: {
            /** Detail */
            detail: string;
            /**
             * Fill Observation
             * @enum {string}
             */
            fill_observation: "fills_on_working_orders" | "no_fills_on_working_orders" | "not_applicable" | "unavailable";
            /**
             * Fill Observation Basis
             * @description Only current acknowledged working orders; not session fill history.
             * @constant
             */
            fill_observation_basis: "current_working_orders";
            /** Label */
            label: string;
            /**
             * No Quote Seconds
             * @description Continuously sampled absence of all working quotes; resets on gaps, restart or generation change.
             */
            no_quote_seconds: number | null;
            /** Proposal Observed At */
            proposal_observed_at: string | null;
            /** Sides */
            sides: {
                [key: string]: components["schemas"]["QuoteSideActivityOutput"];
            };
            /**
             * State
             * @enum {string}
             */
            state: "orders_working" | "one_sided" | "no_quotes" | "unavailable" | "idle";
            /** Warning Active */
            warning_active: boolean;
            /**
             * Warning After Seconds
             * @description Deployment monitoring threshold; 0 disables warnings. Does not change trading limits.
             */
            warning_after_seconds: number;
        };
        /** QuoteGuardConfig */
        QuoteGuardConfigOutput: {
            /** @default null */
            bootstrap: components["schemas"]["BootstrapConfigOutput"] | null;
            /**
             * Enabled
             * @default false
             */
            enabled: boolean;
            /**
             * Max Book Levels
             * @default 50
             */
            max_book_levels: number;
            /**
             * Max Deviation Bps
             * @default null
             */
            max_deviation_bps: string | null;
            /**
             * Max Own Orders
             * @default 200
             */
            max_own_orders: number;
            /**
             * Mode
             * @default strict
             * @enum {string}
             */
            mode: "strict" | "bootstrap_then_market";
            /**
             * Recovery Delay Seconds
             * @default 5
             */
            recovery_delay_seconds: string;
            /**
             * Resume Deviation Bps
             * @default null
             */
            resume_deviation_bps: string | null;
        };
        /** QuoteGuardStatus */
        QuoteGuardStatusOutput: {
            /** Book Observed At */
            book_observed_at: string | null;
            /** @default null */
            bootstrap: components["schemas"]["BootstrapGuardStatusOutput"] | null;
            /**
             * Can Admit
             * @default false
             */
            can_admit: boolean;
            /**
             * Data Quality
             * @description Known-order filtering is a bounded observation, not proof of independent fair value.
             * @enum {string}
             */
            data_quality: "known_order_filtered" | "unavailable";
            /** Detail */
            detail: string | null;
            /** Deviation Bps */
            deviation_bps: string | null;
            /** Enabled */
            enabled: boolean;
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Market Mid */
            market_mid: string | null;
            /**
             * Mode
             * @enum {string}
             */
            mode: "strict" | "bootstrap_then_market";
            /**
             * Qualification Pending
             * @default false
             */
            qualification_pending: boolean;
            /** Ready */
            ready: boolean;
            /** Reason Code */
            reason_code: string | null;
            /** Recovery Started At */
            recovery_started_at: string | null;
            /** Recovery Valid Samples */
            recovery_valid_samples: number;
        };
        /** QuoteSideActivity */
        QuoteSideActivityOutput: {
            /** Detail */
            detail: string;
            /**
             * @description Read-only local evidence for a missing side; null for working, idle, stale or legacy observations. Pending does not prove exchange admission.
             * @default null
             */
            diagnosis: components["schemas"]["QuoteWaitDiagnosisOutput"] | null;
            /** Order Count */
            order_count: number | null;
            /**
             * Proposed Count
             * @description Last controller proposals, not exchange-accepted orders; null when outdated.
             */
            proposed_count: number | null;
            /** Reason Codes */
            reason_codes: string[];
            /**
             * State
             * @enum {string}
             */
            state: "working" | "blocked" | "pending" | "unavailable" | "idle";
        };
        /** QuoteWaitDiagnosis */
        QuoteWaitDiagnosisOutput: {
            /**
             * Error Type
             * @description Fresh proposal exception class only; never exception text or a guessed root cause.
             * @default null
             */
            error_type: string | null;
            /**
             * Next Action
             * @description English operator guidance; does not grant or change trading authority.
             */
            next_action: string;
            /**
             * Observed Seconds
             * @description Consecutive sampled time in this stage, not order age, wire latency or measured downtime. Resets on observation boundaries.
             */
            observed_seconds: number;
            /**
             * Source
             * @enum {string}
             */
            source: "order_tracker" | "executor_event" | "controller_observation" | "unavailable";
            /**
             * Stage
             * @enum {string}
             */
            stage: "cancel_confirmation" | "submission_pending" | "executor_report" | "executor_activity" | "cooldown" | "proposal_confirmation" | "trading_conditions" | "unknown";
        };
        /** RandomSelection */
        RandomSelectionOutput: {
            /**
             * Profile Ids
             * @description Profiles available for random selection; each has equal selection probability.
             */
            profile_ids: string[];
            /**
             * Step Duration Seconds
             * @default 600
             */
            step_duration_seconds: number;
        };
        /** ReconciliationDifference */
        ReconciliationDifferenceOutput: {
            /** Exchange */
            exchange: string | null;
            /**
             * Fill Id
             * @default null
             */
            fill_id: string | null;
            /** Kind */
            kind: string;
            /** Local */
            local: string | null;
            /**
             * Order Id
             * @default null
             */
            order_id: string | null;
            /**
             * Pair
             * @default null
             */
            pair: string | null;
            /**
             * Side
             * @default null
             */
            side: string | null;
        };
        /** ReconciliationMarket */
        ReconciliationMarketOutput: {
            /** Complete */
            complete: boolean;
            /** Differences */
            differences: components["schemas"]["ReconciliationDifferenceOutput"][];
            /** Exchange Observed At */
            exchange_observed_at: string | null;
            /** Fills Checked */
            fills_checked: number;
            /** Local Observed At */
            local_observed_at: string | null;
            /**
             * Market
             * @enum {string}
             */
            market: "spot" | "perpetual";
            /** Orders Checked */
            orders_checked: number;
            /** Positions Checked */
            positions_checked: number;
            /** Reasons */
            reasons: string[];
            /**
             * State
             * @enum {string}
             */
            state: "matched" | "mismatch" | "incomplete" | "unavailable";
        };
        /** ReconciliationReceipt */
        ReconciliationReceiptOutput: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Error
             * @default null
             */
            error: string | null;
            /** Id */
            id: string;
            payload: components["schemas"]["ReconciliationRequestOutput"];
            report: components["schemas"]["ReconciliationReportOutput"];
            /**
             * Status
             * @enum {string}
             */
            status: "accepted" | "applying" | "succeeded" | "failed";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ReconciliationReport */
        ReconciliationReportOutput: {
            /**
             * Finished At
             * @default null
             */
            finished_at: string | null;
            /** Markets */
            markets?: components["schemas"]["ReconciliationMarketOutput"][];
            /**
             * Scope
             * @default bot_owned_orders_and_configured_perpetual_exposure
             * @constant
             */
            scope: "bot_owned_orders_and_configured_perpetual_exposure";
            /**
             * Started At
             * @default null
             */
            started_at: string | null;
            /**
             * State
             * @default not_checked
             * @enum {string}
             */
            state: "not_checked" | "checking" | "matched" | "mismatch" | "incomplete" | "unavailable";
        };
        /** ReconciliationRequest */
        ReconciliationRequest: {
            /**
             * Lookback Seconds
             * @default 3600
             */
            lookback_seconds: number;
            /**
             * Market
             * @default all
             * @enum {string}
             */
            market: "spot" | "perpetual" | "all";
            /**
             * Max Orders
             * @default 100
             */
            max_orders: number;
            /**
             * Max Pages
             * @default 5
             */
            max_pages: number;
            /**
             * Pair
             * @default null
             */
            pair: string | null;
        };
        /** ReconciliationRequest */
        ReconciliationRequestOutput: {
            /**
             * Lookback Seconds
             * @default 3600
             */
            lookback_seconds: number;
            /**
             * Market
             * @default all
             * @enum {string}
             */
            market: "spot" | "perpetual" | "all";
            /**
             * Max Orders
             * @default 100
             */
            max_orders: number;
            /**
             * Max Pages
             * @default 5
             */
            max_pages: number;
            /**
             * Pair
             * @default null
             */
            pair: string | null;
        };
        /** ReconciliationResponse */
        ReconciliationResponseOutput: {
            /** @default null */
            check: components["schemas"]["ReconciliationReceiptOutput"] | null;
            report?: components["schemas"]["ReconciliationReportOutput"];
        };
        /** RecordedFee */
        RecordedFeeOutput: {
            /**
             * Fee Type
             * @default null
             */
            fee_type: string | null;
            /** Flat Fees */
            flat_fees?: components["schemas"]["FeeAmountOutput"][];
            /**
             * Percent
             * @default 0
             */
            percent: string;
            /**
             * Percent Token
             * @default null
             */
            percent_token: string | null;
        };
        /**
         * RecordedStartCommand
         * @description Read-only compatibility with receipts created before YAML-only commands.
         */
        RecordedStartCommandOutput: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            action: "start";
            /**
             * Duration Mode
             * @description Timed uses YAML duration bounds/default. Until stopped requires YAML permission and must omit duration_seconds.
             * @default timed
             * @enum {string}
             */
            duration_mode: "timed" | "until_stopped";
            /**
             * Duration Seconds
             * @description Timed-session duration within YAML bounds; omit for its default. Must be absent/null for until_stopped.
             * @default null
             */
            duration_seconds: number | null;
            /**
             * Expected Catalogue Hash
             * @description Precondition from /profiles. Send it to reject an unseen YAML revision; this does not override configuration.
             * @default null
             */
            expected_catalogue_hash: string | null;
            /** Expected Version */
            expected_version: number;
            /**
             * Market Mode
             * @enum {string}
             */
            market_mode: "spot" | "perpetual" | "both";
            /**
             * Profile Id
             * @description Deployed profile ID for fixed selection, such as follow_market, sideways or follow_token. Omit for random.
             * @default null
             */
            profile_id: string | null;
            /**
             * Profile Ids
             * @default null
             */
            profile_ids: string[] | null;
            /**
             * References
             * @default null
             */
            references: components["schemas"]["ReferencePairOutput"][] | null;
            /**
             * Selection
             * @description Fixed runs one profile; random chooses timed steps from a profile pool.
             * @default fixed
             * @enum {string}
             */
            selection: "fixed" | "random";
            /**
             * Step Duration Seconds
             * @default null
             */
            step_duration_seconds: number | null;
        };
        /** RecordedSwitchCommand */
        RecordedSwitchCommandOutput: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            action: "switch";
            /**
             * Duration Mode
             * @description Timed uses YAML duration bounds/default. Until stopped requires YAML permission and must omit duration_seconds.
             * @default timed
             * @enum {string}
             */
            duration_mode: "timed" | "until_stopped";
            /**
             * Duration Seconds
             * @description Timed-session duration within YAML bounds; omit for its default. Must be absent/null for until_stopped.
             * @default null
             */
            duration_seconds: number | null;
            /**
             * Expected Catalogue Hash
             * @description Precondition from /profiles. Send it to reject an unseen YAML revision; this does not override configuration.
             * @default null
             */
            expected_catalogue_hash: string | null;
            /** Expected Version */
            expected_version: number;
            /**
             * Market Mode
             * @enum {string}
             */
            market_mode: "spot" | "perpetual" | "both";
            /**
             * Profile Id
             * @description Deployed profile ID for fixed selection, such as follow_market, sideways or follow_token. Omit for random.
             * @default null
             */
            profile_id: string | null;
            /**
             * Profile Ids
             * @default null
             */
            profile_ids: string[] | null;
            /**
             * References
             * @default null
             */
            references: components["schemas"]["ReferencePairOutput"][] | null;
            /**
             * Selection
             * @description Fixed runs one profile; random chooses timed steps from a profile pool.
             * @default fixed
             * @enum {string}
             */
            selection: "fixed" | "random";
            /** Session Id */
            session_id: string;
            /**
             * Step Duration Seconds
             * @default null
             */
            step_duration_seconds: number | null;
        };
        /** RecoveryBlocker */
        RecoveryBlockerOutput: {
            /** Code */
            code: string;
            /** Detail */
            detail: string;
            /** Next Action */
            next_action: string;
        };
        /** RecoveryCheckRequest */
        RecoveryCheckRequest: {
            /** Expected Version */
            expected_version: number;
        };
        /** RecoveryOperation */
        RecoveryOperationOutput: {
            /**
             * Action
             * @enum {string}
             */
            action: "recovery_check" | "recovery_reset";
            /** Blockers */
            blockers?: components["schemas"]["RecoveryBlockerOutput"][];
            /** Can Recover */
            can_recover: boolean;
            /**
             * Confirmation Token
             * @default null
             */
            confirmation_token: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Detail */
            detail: string;
            /**
             * Expires At
             * @default null
             */
            expires_at: string | null;
            /** Id */
            id: string;
            /** Label */
            label: string;
            /** Proposed Changes */
            proposed_changes?: components["schemas"]["RecoveryPositionRepairOutput"][];
            /** Repaired Positions */
            repaired_positions?: components["schemas"]["RecoveryPositionRepairOutput"][];
            /** Stage */
            stage: string;
            /**
             * Status
             * @enum {string}
             */
            status: "accepted" | "applying" | "succeeded" | "failed";
            /**
             * Trading State
             * @default stopped
             * @constant
             */
            trading_state: "stopped";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Verification */
            verification?: {
                [key: string]: unknown;
            };
        };
        /** RecoveryOverview */
        RecoveryOverviewOutput: {
            /** Blockers */
            blockers: components["schemas"]["RecoveryBlockerOutput"][];
            /** Check Available */
            check_available: boolean;
            /** Control Version */
            control_version: number;
            /** Halted */
            halted: boolean;
            /** @default null */
            operation: components["schemas"]["RecoveryOperationOutput"] | null;
            /** Reset Available */
            reset_available: boolean;
        };
        /** RecoveryPositionRepair */
        RecoveryPositionRepairOutput: {
            after: components["schemas"]["RecoveryPositionValuesOutput"];
            before: components["schemas"]["RecoveryPositionValuesOutput"];
            /**
             * Connector
             * @enum {string}
             */
            connector: "okx" | "okx_perpetual";
            /** Controller Id */
            controller_id: string;
            /** Id */
            id: string;
            /** Pair */
            pair: string;
            /**
             * Valuation Basis
             * @constant
             */
            valuation_basis: "reconstructed_average_entry_not_live_pnl";
        };
        /** RecoveryPositionValues */
        RecoveryPositionValuesOutput: {
            /** Amount */
            amount: string;
            /** Breakeven Price */
            breakeven_price: string;
            /** Cum Fees Quote */
            cum_fees_quote: string;
            /** Realized Pnl Quote */
            realized_pnl_quote: string;
            /**
             * Side
             * @enum {string}
             */
            side: "BUY" | "SELL";
            /** Unrealized Pnl Quote */
            unrealized_pnl_quote: string;
            /** Volume Traded Quote */
            volume_traded_quote: string;
        };
        /** RecoveryResetRequest */
        RecoveryResetRequest: {
            /** Check Id */
            check_id: string;
            /** Confirmation Token */
            confirmation_token: string;
            /** Expected Version */
            expected_version: number;
        };
        /** RecoverySummary */
        RecoverySummaryOutput: {
            /**
             * Check Available
             * @default false
             */
            check_available: boolean;
            /** Detail */
            detail: string;
            /** Halted */
            halted: boolean;
            /**
             * Next Action
             * @enum {string}
             */
            next_action: "none" | "contact_support" | "check_recovery" | "wait";
            /** Pending Mutations */
            pending_mutations: boolean;
            /**
             * Reset Available
             * @default false
             */
            reset_available: boolean;
            /**
             * Status Url
             * @default /api/v1/admin/recovery
             */
            status_url: string;
        };
        /** ReferenceAnchors */
        ReferenceAnchorsOutput: {
            /** Life */
            life: string;
            /** Sources */
            sources: {
                [key: string]: string;
            };
        };
        /** ReferencePair */
        ReferencePairOutput: {
            /** Pair */
            pair: string;
            /** Weight */
            weight: string;
        };
        /** ReferencePairsResponse */
        ReferencePairsResponseOutput: {
            /**
             * Connector
             * @default okx
             * @constant
             */
            connector: "okx";
            /** Items */
            items: string[];
            /** Next After */
            next_after: string | null;
            /** Observed At */
            observed_at: string | null;
            /**
             * Price Source
             * @default spot_order_book_midpoint
             * @constant
             */
            price_source: "spot_order_book_midpoint";
            /** Stale */
            stale: boolean;
        };
        /** RuntimeIdentity */
        RuntimeIdentityOutput: {
            /**
             * Basis
             * @default process_configuration
             * @constant
             */
            basis: "process_configuration";
            /**
             * Revision
             * @default null
             */
            revision: string | null;
            /**
             * Trading Environment
             * @enum {string}
             */
            trading_environment: "live" | "demo" | "unknown";
        };
        /** RuntimeIncident */
        RuntimeIncident: {
            /**
             * Active Fallback
             * @default null
             */
            active_fallback: string | null;
            /**
             * Detail
             * @default null
             */
            detail: string | null;
            /**
             * First Seen At
             * Format: date-time
             */
            first_seen_at: string;
            /**
             * Last Healthy At
             * @default null
             */
            last_healthy_at: string | null;
            /**
             * Last Seen At
             * Format: date-time
             */
            last_seen_at: string;
            /**
             * Next Retry At
             * @default null
             */
            next_retry_at: string | null;
            /** Operator Action Required */
            operator_action_required: boolean;
            /** Reason Code */
            reason_code: string;
            /** Retry Count */
            retry_count: number;
            /** Scope */
            scope: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "warning" | "error";
        };
        /** RuntimeIncident */
        RuntimeIncidentOutput: {
            /**
             * Active Fallback
             * @default null
             */
            active_fallback: string | null;
            /**
             * Detail
             * @default null
             */
            detail: string | null;
            /**
             * First Seen At
             * Format: date-time
             */
            first_seen_at: string;
            /**
             * Last Healthy At
             * @default null
             */
            last_healthy_at: string | null;
            /**
             * Last Seen At
             * Format: date-time
             */
            last_seen_at: string;
            /**
             * Next Retry At
             * @default null
             */
            next_retry_at: string | null;
            /** Operator Action Required */
            operator_action_required: boolean;
            /** Reason Code */
            reason_code: string;
            /** Retry Count */
            retry_count: number;
            /** Scope */
            scope: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "warning" | "error";
        };
        /** ServiceStatus */
        ServiceStatus: {
            /** Incidents */
            incidents?: components["schemas"]["RuntimeIncident"][];
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /**
             * Observer Age Seconds
             * @default null
             */
            observer_age_seconds: number | null;
            /** Persisted State Available */
            persisted_state_available: boolean;
            /** Phase */
            phase: string;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
            /**
             * Stale
             * @default false
             */
            stale: boolean;
            /**
             * State Source
             * @enum {string}
             */
            state_source: "postgres" | "process";
        };
        /** ServiceStatus */
        ServiceStatusOutput: {
            /** Incidents */
            incidents?: components["schemas"]["RuntimeIncidentOutput"][];
            /**
             * Observed At
             * @default null
             */
            observed_at: string | null;
            /**
             * Observer Age Seconds
             * @default null
             */
            observer_age_seconds: number | null;
            /** Persisted State Available */
            persisted_state_available: boolean;
            /** Phase */
            phase: string;
            /**
             * Reason Code
             * @default null
             */
            reason_code: string | null;
            /**
             * Stale
             * @default false
             */
            stale: boolean;
            /**
             * State Source
             * @enum {string}
             */
            state_source: "postgres" | "process";
        };
        /** SessionConfigurationResponse */
        SessionConfigurationResponseOutput: {
            catalogue: components["schemas"]["MMProfilesConfigOutput"];
            /** Catalogue Hash */
            catalogue_hash: string;
            /** Config Hash */
            config_hash: string;
            configuration_policy?: components["schemas"]["ConfigurationPolicyOutput"];
            /** Legacy Overrides */
            legacy_overrides: string[];
            /**
             * Profile Context
             * @enum {string}
             */
            profile_context: "current" | "at_creation";
            /** Profile Id */
            profile_id: string;
            request: components["schemas"]["MMSessionRequestOutput"];
            /** Session Id */
            session_id: string;
            /**
             * Source
             * @enum {string}
             */
            source: "current_session" | "session_created_event";
        };
        /** SessionState */
        SessionStateOutput: {
            /** Anchors */
            anchors: {
                [key: string]: components["schemas"]["ReferenceAnchorsOutput"];
            };
            /** Config Hash */
            config_hash: string;
            /** Expires At */
            expires_at: string | null;
            /** Generation */
            generation: number;
            /** Id */
            id: string;
            /** Profile Id */
            profile_id: string;
            request: components["schemas"]["MMSessionRequestOutput"];
            /** Started At */
            started_at: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "preparing" | "running" | "stopping" | "stopped" | "expired";
            /** Step Expires At */
            step_expires_at: string | null;
            /** Step Index */
            step_index: number;
        };
        /** SessionSummary */
        SessionSummaryOutput: {
            /** Config Hash */
            config_hash: string;
            /** Configuration Url */
            configuration_url: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Events Url */
            events_url: string;
            /** Id */
            id: string;
            /** Is Current */
            is_current: boolean;
            /**
             * Last Event At
             * Format: date-time
             */
            last_event_at: string;
            /** Profile Id */
            profile_id: string;
            request: components["schemas"]["MMSessionRequestOutput"];
            /**
             * State Source
             * @enum {string}
             */
            state_source: "current_session" | "last_recorded_transition";
            /**
             * Status
             * @enum {string}
             */
            status: "preparing" | "running" | "stopping" | "stopped" | "expired" | "replaced" | "unknown";
        };
        /** SessionsPage */
        SessionsPageOutput: {
            /**
             * Coverage
             * @description Legacy sessions without a creation event may be absent. This is not historical quote coverage.
             * @constant
             */
            coverage: "recorded_session_creations";
            /** Current Session Id */
            current_session_id: string | null;
            /** Items */
            items: components["schemas"]["SessionSummaryOutput"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** SidewaysRange */
        SidewaysRangeOutput: {
            /** Lower Price */
            lower_price: string;
            /** Resume Lower Price */
            resume_lower_price: string;
            /** Resume Upper Price */
            resume_upper_price: string;
            /** Upper Price */
            upper_price: string;
        };
        /** SpotOutflowBinding */
        SpotOutflowBindingOutput: {
            /** Account Id */
            account_id: string;
            /**
             * Environment
             * @enum {string}
             */
            environment: "live" | "demo";
            /** Trading Pair */
            trading_pair: string;
        };
        /** SpotOutflowConfig */
        SpotOutflowConfigOutput: {
            /** Fee Allowance Rate */
            fee_allowance_rate: string;
            /** Max Net Outflow Quote */
            max_net_outflow_quote: string;
            /** Window Seconds */
            window_seconds: number;
        };
        /** SpotOutflowPolicy */
        SpotOutflowPolicyOutput: {
            /** Fee Allowance Rate */
            fee_allowance_rate: string;
            /** Max Net Outflow Quote */
            max_net_outflow_quote: string;
            /** Trading Pair */
            trading_pair: string;
            /** Window Seconds */
            window_seconds: number;
        };
        /** SpotOutflowStatus */
        SpotOutflowStatusOutput: {
            binding: components["schemas"]["SpotOutflowBindingOutput"] | null;
            /** Buy Allowed */
            buy_allowed: boolean;
            /** Coverage Start Ms */
            coverage_start_ms: number | null;
            /** Fill Count */
            fill_count: number;
            /**
             * Last Buy Denial
             * @default null
             */
            last_buy_denial: string | null;
            /** Non Usdt Fees */
            non_usdt_fees: {
                [key: string]: string;
            };
            /** Observed At */
            observed_at: string | null;
            /**
             * Pending Terminal Orders
             * @default 0
             */
            pending_terminal_orders: number;
            policy: components["schemas"]["SpotOutflowPolicyOutput"];
            /** Ready */
            ready: boolean;
            /** Reason Code */
            reason_code: string | null;
            /** Recorded Net Outflow Quote */
            recorded_net_outflow_quote: string | null;
            /** Remaining Quote */
            remaining_quote: string | null;
            /** Reserved Quote */
            reserved_quote: string | null;
            /** Stale */
            stale: boolean;
            /** Window End Ms */
            window_end_ms: number | null;
            /** Window Start Ms */
            window_start_ms: number | null;
        };
        /** SpreadPolicyConfig */
        SpreadPolicyConfigOutput: {
            /**
             * Fee Floor
             * @default false
             */
            fee_floor: boolean;
            volatility?: components["schemas"]["VolatilitySpreadConfigOutput"];
        };
        /** SpreadPolicyStatus */
        SpreadPolicyStatusOutput: {
            /** Buy Spreads */
            buy_spreads: string[];
            /** Completed At */
            completed_at: string | null;
            /** @default null */
            defense: components["schemas"]["VolatilityDefenseStatusOutput"] | null;
            /** Enabled */
            enabled: boolean;
            /** Fee Floor */
            fee_floor: boolean;
            /** Multiplier */
            multiplier: string | null;
            /**
             * Raw Natr
             * @description Fresh completed-candle NATR as a fraction, before multiplier clamping; null when unavailable.
             * @default null
             */
            raw_natr: string | null;
            /** Sell Spreads */
            sell_spreads: string[];
            /**
             * Stale
             * @default false
             */
            stale: boolean;
            /**
             * State
             * @enum {string}
             */
            state: "disabled" | "static" | "dynamic" | "static_fallback" | "waiting";
            /** Valid Until */
            valid_until: string | null;
        };
        /** StartCommand */
        StartCommand: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            action: "start";
            /**
             * Duration Mode
             * @description Timed uses YAML duration bounds/default. Until stopped requires YAML permission and must omit duration_seconds.
             * @default timed
             * @enum {string}
             */
            duration_mode: "timed" | "until_stopped";
            /**
             * Duration Seconds
             * @description Timed-session duration within YAML bounds; omit for its default. Must be absent/null for until_stopped.
             * @default null
             */
            duration_seconds: number | null;
            /**
             * Expected Catalogue Hash
             * @description Precondition from /profiles. Send it to reject an unseen YAML revision; this does not override configuration.
             * @default null
             */
            expected_catalogue_hash: string | null;
            /** Expected Version */
            expected_version: number;
            /**
             * Market Mode
             * @enum {string}
             */
            market_mode: "spot" | "perpetual" | "both";
            /**
             * Profile Id
             * @description Deployed profile ID for fixed selection, such as follow_market, sideways or follow_token. Omit for random.
             * @default null
             */
            profile_id: string | null;
            /**
             * Selection
             * @description Fixed runs one profile; random chooses timed steps from a profile pool.
             * @default fixed
             * @enum {string}
             */
            selection: "fixed" | "random";
        };
        /** StatusResponse */
        StatusResponseOutput: {
            /** Acknowledged Version */
            acknowledged_version: number;
            /** Actions */
            actions?: {
                [key: string]: components["schemas"]["ActionAvailabilityOutput"];
            };
            /** @default null */
            automatic_recovery: components["schemas"]["AutomaticRecoveryStatusOutput"] | null;
            /** Bot Id */
            bot_id: string;
            /** Control Version */
            control_version: number;
            /**
             * Desired State
             * @enum {string}
             */
            desired_state: "running" | "stopped";
            /** @default null */
            drawdown: components["schemas"]["DrawdownStatusOutput"] | null;
            /**
             * Error
             * @default null
             */
            error: string | null;
            /** @default null */
            funding_sync: components["schemas"]["FundingSyncStatusOutput"] | null;
            /** Heartbeat Age Seconds */
            heartbeat_age_seconds: number | null;
            /** Markets */
            markets?: components["schemas"]["MarketReadinessOutput"][];
            /** @default null */
            observation: components["schemas"]["ObservationStatusOutput"] | null;
            /** @default null */
            observed_session: components["schemas"]["SessionStateOutput"] | null;
            /** @default null */
            operator: components["schemas"]["OperatorStatusOutput"] | null;
            /** Owner Available */
            owner_available: boolean;
            /** @default null */
            owner_identity: components["schemas"]["RuntimeIdentityOutput"] | null;
            /** Phase */
            phase: string;
            /** @default null */
            process_identity: components["schemas"]["RuntimeIdentityOutput"] | null;
            /** Quote Activity */
            quote_activity?: {
                [key: string]: components["schemas"]["QuoteActivityOutput"];
            };
            /** @default null */
            recovery: components["schemas"]["RecoverySummaryOutput"] | null;
            /** Running */
            running: boolean;
            /**
             * Server Time
             * @default null
             */
            server_time: string | null;
            /** @default null */
            service: components["schemas"]["ServiceStatusOutput"] | null;
            /** @default null */
            session: components["schemas"]["SessionStateOutput"] | null;
            /**
             * Session Configuration Url
             * @default null
             */
            session_configuration_url: string | null;
            /** @default null */
            spot_outflow: components["schemas"]["SpotOutflowStatusOutput"] | null;
            /**
             * Time Remaining Seconds
             * @default null
             */
            time_remaining_seconds: number | null;
        };
        /** StopCommand */
        StopCommand: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            action: "stop";
            /** Expected Version */
            expected_version: number;
            /** Session Id */
            session_id: string;
        };
        /** StopCommand */
        StopCommandOutput: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            action: "stop";
            /** Expected Version */
            expected_version: number;
            /** Session Id */
            session_id: string;
        };
        /** SwitchCommand */
        SwitchCommand: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            action: "switch";
            /**
             * Duration Mode
             * @description Timed uses YAML duration bounds/default. Until stopped requires YAML permission and must omit duration_seconds.
             * @default timed
             * @enum {string}
             */
            duration_mode: "timed" | "until_stopped";
            /**
             * Duration Seconds
             * @description Timed-session duration within YAML bounds; omit for its default. Must be absent/null for until_stopped.
             * @default null
             */
            duration_seconds: number | null;
            /**
             * Expected Catalogue Hash
             * @description Precondition from /profiles. Send it to reject an unseen YAML revision; this does not override configuration.
             * @default null
             */
            expected_catalogue_hash: string | null;
            /** Expected Version */
            expected_version: number;
            /**
             * Market Mode
             * @enum {string}
             */
            market_mode: "spot" | "perpetual" | "both";
            /**
             * Profile Id
             * @description Deployed profile ID for fixed selection, such as follow_market, sideways or follow_token. Omit for random.
             * @default null
             */
            profile_id: string | null;
            /**
             * Selection
             * @description Fixed runs one profile; random chooses timed steps from a profile pool.
             * @default fixed
             * @enum {string}
             */
            selection: "fixed" | "random";
            /** Session Id */
            session_id: string;
        };
        /**
         * TradeType
         * @enum {integer}
         */
        TradeTypeOutput: 1 | 2 | 3;
        /** TrailingStop */
        TrailingStopOutput: {
            /** Activation Price */
            activation_price: string;
            /** Trailing Delta */
            trailing_delta: string;
        };
        /** VolatilityDefenseConfig */
        VolatilityDefenseConfigOutput: {
            /** Recovery Seconds */
            recovery_seconds: number;
            /** Reduce At Natr */
            reduce_at_natr: string;
            /** Reduced Size Factor */
            reduced_size_factor: string;
            /** Resume Below Natr */
            resume_below_natr: string;
            /** Stop At Natr */
            stop_at_natr: string;
        };
        /** VolatilityDefenseStatus */
        VolatilityDefenseStatusOutput: {
            /**
             * Automatic Recovery Expected
             * @description Conditional on fresh data and the existing session/ownership/risk gates. Not an ETA or a HALT reset.
             */
            automatic_recovery_expected: boolean;
            /**
             * Cancellation Pending
             * @description Executor cancellation/retirement pending; not proof that all exchange orders have been removed. Null when observation is stale.
             */
            cancellation_pending: boolean | null;
            /** Effective Allocation Quote */
            effective_allocation_quote: string;
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Label */
            label: string;
            /** Next Action */
            next_action: string;
            /** Paused Latch Persisted */
            paused_latch_persisted: boolean | null;
            /** Reason Code */
            reason_code: string | null;
            /** Recovery Elapsed Seconds */
            recovery_elapsed_seconds: number;
            /** Recovery Samples */
            recovery_samples: number;
            /** Size Factor */
            size_factor: string;
            /**
             * State
             * @enum {string}
             */
            state: "normal" | "reduced" | "paused" | "waiting" | "static_fallback" | "checkpoint_pending" | "unavailable";
        };
        /** VolatilitySpreadConfig */
        VolatilitySpreadConfigOutput: {
            /** @default null */
            defense: components["schemas"]["VolatilityDefenseConfigOutput"] | null;
            /**
             * Enabled
             * @default false
             */
            enabled: boolean;
            /**
             * Fallback Buy Spreads
             * @default null
             */
            fallback_buy_spreads: string[] | null;
            /**
             * Fallback Sell Spreads
             * @default null
             */
            fallback_sell_spreads: string[] | null;
            /**
             * Interval
             * @default 1m
             * @enum {string}
             */
            interval: "1s" | "1m" | "3m" | "5m" | "15m" | "30m" | "1h";
            /**
             * Length
             * @default 14
             */
            length: number;
            /**
             * Max Age Seconds
             * @default 120
             */
            max_age_seconds: number;
            /**
             * Max Multiplier
             * @default 3
             */
            max_multiplier: string;
            /**
             * Min Multiplier
             * @default 0.5
             */
            min_multiplier: string;
            /**
             * On Unavailable
             * @default wait
             * @enum {string}
             */
            on_unavailable: "static" | "wait";
            /**
             * Target Natr
             * @default 0.01
             */
            target_natr: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    listAdminAccounts: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountsPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    addAdminAccount: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AddAccountRequest"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    changeAdminAccount: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                account_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChangeAccountRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getAdminBootstrap: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BootstrapResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    adminLogin: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PrincipalResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getAdminIdentity: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PrincipalResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getAdminAudit: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: string | null;
                from_time?: string | null;
                to_time?: string | null;
                actor_id?: string | null;
                action?: string | null;
                outcome?: string | null;
                target_id?: string | null;
                operation_id?: string | null;
                exchange?: string | null;
                connector?: string | null;
                environment?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AuditPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getBalances: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BalancesResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listCommands: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: string | null;
                action?: ("start" | "switch" | "stop") | null;
                status?: ("accepted" | "applying" | "succeeded" | "failed" | "superseded") | null;
                session_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandsPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    submitCommand: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StartCommand"] | components["schemas"]["SwitchCommand"] | components["schemas"]["StopCommand"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getCommand: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                command_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listCredentialInputs: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialCatalogueOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getCredentialOperation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    retryCredentialCleanup: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialRetryRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getExchangeCredentialStatus: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                exchange: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExchangeCredentialStatusOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    setExchangeCredentials: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path: {
                connector: string;
                exchange: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialSetRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    repairCleanupCredentials: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path: {
                connector: string;
                exchange: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialCleanupKeyRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    disconnectExchangeCredentials: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path: {
                connector: string;
                exchange: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialDisconnectRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getSwaggerUI: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "text/html": string;
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getSwaggerAsset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                asset: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": string;
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listFills: {
        parameters: {
            query?: {
                market?: "spot" | "perpetual" | "all";
                pair?: string | null;
                from?: string | null;
                to?: string | null;
                limit?: number;
                cursor?: string | null;
                order_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FillsPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getOpenAPI: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listOrders: {
        parameters: {
            query?: {
                market?: "spot" | "perpetual" | "all";
                pair?: string | null;
                from?: string | null;
                to?: string | null;
                limit?: number;
                cursor?: string | null;
                state?: "open" | "closed" | "all";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrdersPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getOrder: {
        parameters: {
            query?: {
                timeline_limit?: number;
            };
            header?: never;
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderDetailOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getPerformance: {
        parameters: {
            query?: {
                market?: "spot" | "perpetual" | "all";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PerformanceResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getPositions: {
        parameters: {
            query?: {
                market?: "spot" | "perpetual" | "all";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PositionsResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listProfiles: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProfilesResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getReconciliation: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReconciliationResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    requestReconciliation: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReconciliationRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReconciliationReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getReconciliationById: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                check_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReconciliationReceiptOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getRecovery: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryOverviewOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    checkRecovery: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RecoveryCheckRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryOperationOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getRecoveryOperation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryOperationOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    resetRecovery: {
        parameters: {
            query?: never;
            header: {
                /** @description Stable retry identity; reuse only for the identical request body */
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RecoveryResetRequest"];
            };
        };
        responses: {
            /** @description Accepted; poll command result */
            202: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryOperationOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listReferencePairs: {
        parameters: {
            query?: {
                search?: string;
                after?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReferencePairsResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    listSessions: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionsPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getSessionConfiguration: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionConfigurationResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getSessionEvents: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: string | null;
                event_type?: string | null;
                source?: ("admin_api" | "scheduler" | "observer" | "cli" | "control") | null;
            };
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MMEventsPageOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getDailyStatistics: {
        parameters: {
            query?: {
                market?: "spot" | "perpetual" | "all";
                from?: string | null;
                to?: string | null;
                pair?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DailyResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getLiquidity: {
        parameters: {
            query?: {
                market?: "spot" | "perpetual" | "all";
                session_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LiquidityResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    getRuntimeStatus: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StatusResponseOutput"];
                };
            };
            /** @description Invalid JSON */
            400: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Basic authentication required */
            401: {
                headers: {
                    "WWW-Authenticate"?: string;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Origin not permitted */
            403: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Resource not found */
            404: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Control or retry identity conflict */
            409: {
                headers: {
                    /** @description Seconds until a new check may be requested, when present */
                    "Retry-After"?: number;
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "control_version_changed",
                     *       "error": "control_or_idempotency_conflict",
                     *       "fields": [],
                     *       "request_id": "example-request",
                     *       "retry": "refresh_state"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unsupported content type */
            415: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "code": "yaml_configuration_read_only",
                     *       "error": "validation_failed",
                     *       "fields": [
                     *         {
                     *           "code": "yaml_configuration_read_only",
                     *           "path": "references"
                     *         }
                     *       ],
                     *       "request_id": "example-request",
                     *       "retry": "fix_request"
                     *     }
                     */
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service/data unavailable */
            503: {
                headers: {
                    /** @description Server-generated diagnostic identifier */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
}
