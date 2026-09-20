"""Canonical financial metrics and the us-gaap concepts that report them.

Vendored verbatim from the xbrl_normaliser project, where this mapping was built by hand against real filings
-- the comments naming specific companies mark concessions to how those filers actually tag, and every one of
them was paid for with a wrong number. It is copied rather than imported because it is reference data, not
behaviour: this project needs it pinned to a known state, and xbrl_normaliser has no tests over it.

Structure: METRIC_ANCHORS maps a canonical metric label to (statement, [anchor concepts in priority order]).
The first anchor present in a filing wins. BLOCKLIST names concepts that must never map, and METRIC_PRIORITY
breaks ties when one concept could serve two metrics.
"""

# metric → (statement, [anchor concepts ordered by priority])
METRIC_ANCHORS: dict[str, tuple[str, list[str]]] = {
    # ── Income Statement ──────────────────────────────────────────────────────
    'Revenue': ('IS', [
        'Revenues',
        'RevenueFromContractWithCustomerExcludingAssessedTax',
        'RevenueFromContractWithCustomer',
        'SalesRevenueNet',
        'SalesRevenueGoodsNet',
        'RevenuesNetOfInterestExpense',
    ]),
    'Cost of Revenue': ('IS', [
        'CostOfRevenue',
        'CostOfGoodsAndServicesSold',
        'CostOfGoodsSold',
        'CostOfServices',
        'CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization',
    ]),
    'Gross Profit': ('IS', [
        'GrossProfit',
    ]),
    'R&D Expense': ('IS', [
        'ResearchAndDevelopmentExpense',
        'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost',
        'ResearchAndDevelopmentAssetAcquiredOtherThanThroughBusinessCombinationWrittenOff',
    ]),
    'SG&A Expense': ('IS', [
        'SellingGeneralAndAdministrativeExpense',
        'GeneralAndAdministrativeExpense',
        'SellingAndMarketingExpense',
        'SellingExpense',
    ]),
    'Depreciation & Amortization': ('IS', [
        'DepreciationAndAmortization',
        'DepreciationDepletionAndAmortization',
        'Depreciation',
        'AmortizationOfIntangibleAssets',
        # Capitalized software amortization: filed separately from intangible amortization
        # by companies that capitalize internal-use software (e.g. TXN). YF includes it
        # in "Reconciled Depreciation". Only used as component sum when aggregate absent.
        'CapitalizedComputerSoftwareAmortization1',
    ]),
    'Operating Income': ('IS', [
        'OperatingIncomeLoss',
    ]),
    'Interest Expense': ('IS', [
        'InterestExpense',
        'InterestExpenseDebt',
        'InterestAndDebtExpense',
        'InterestExpenseRelatedParty',
    ]),
    'Interest Income': ('IS', [
        'InvestmentIncomeInterest',
        'InterestAndDividendIncomeOperating',
        'InterestIncomeOperating',
        # InterestIncomeExpenseNet removed: it is net interest (income minus expense),
        # so it can be negative for net borrowers (e.g. MO = -989M). Not pure income.
    ]),
    'Pretax Income': ('IS', [
        'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
        'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments',
        'IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic',
    ]),
    'Income Tax Expense': ('IS', [
        'IncomeTaxExpenseBenefit',
        # IncomeTaxesPaidNet removed: it is a CF supplemental disclosure (cash paid),
        # not the IS accrual expense — picking it produces the wrong value.
    ]),
    'Net Income': ('IS', [
        'NetIncomeLoss',
        'ProfitLoss',
        'IncomeLossFromContinuingOperationsAttributableToParent',
        'IncomeLossFromContinuingOperations',
    ]),
    'EPS Basic': ('IS', [
        'EarningsPerShareBasic',
    ]),
    'EPS Diluted': ('IS', [
        'EarningsPerShareDiluted',
    ]),
    'Shares Basic': ('IS', [
        'WeightedAverageNumberOfSharesOutstandingBasic',
        'CommonStockSharesOutstanding',
    ]),
    'Shares Diluted': ('IS', [
        'WeightedAverageNumberOfDilutedSharesOutstanding',
        'WeightedAverageNumberOfSharesOutstandingDiluted',
    ]),

    # ── Balance Sheet ─────────────────────────────────────────────────────────
    'Cash': ('BS', [
        'CashAndCashEquivalentsAtCarryingValue',
        'CashCashEquivalentsAndShortTermInvestments',
        'Cash',
        'CashAndCashEquivalents',
        # Post-ASU-2016-18 cash flow reconciliation tag: includes restricted cash.
        # Filed by companies (e.g. PG) that do not separately file
        # CashAndCashEquivalentsAtCarryingValue on the BS. YF uses this value.
        'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents',
    ]),
    'Short Term Investments': ('BS', [
        'ShortTermInvestments',
        'MarketableSecuritiesCurrent',
        'AvailableForSaleSecuritiesCurrent',
        'TradingSecuritiesCurrent',
    ]),
    'Accounts Receivable': ('BS', [
        'AccountsReceivableNetCurrent',
        'ReceivablesNetCurrent',
        'AccountsAndNotesReceivableNet',
        'TradeAndOtherReceivablesNetCurrent',
    ]),
    'Inventory': ('BS', [
        'InventoryNet',
        'Inventories',
        'InventoryGross',
    ]),
    'Current Assets': ('BS', [
        'AssetsCurrent',
    ]),
    'PP&E Net': ('BS', [
        'PropertyPlantAndEquipmentNet',
        'PropertyPlantAndEquipmentNetOfAccumulatedDepreciation',
    ]),
    'Goodwill': ('BS', [
        'Goodwill',
    ]),
    'Intangible Assets': ('BS', [
        'IntangibleAssetsNetExcludingGoodwill',
        'FiniteLivedIntangibleAssetsNet',
        'IndefiniteLivedIntangibleAssetsExcludingGoodwill',
    ]),
    'Total Assets': ('BS', [
        'Assets',
    ]),
    'Accounts Payable': ('BS', [
        'AccountsPayableCurrent',
        'AccountsPayableAndAccruedLiabilitiesCurrent',
        'AccountsPayable',
    ]),
    'Short Term Debt': ('BS', [
        'ShortTermBorrowings',
        'DebtCurrent',
        'LongTermDebtCurrent',
        'NotesPayableCurrent',
        'CommercialPaper',
    ]),
    'Current Liabilities': ('BS', [
        'LiabilitiesCurrent',
    ]),
    'Long Term Debt': ('BS', [
        # Prefer non-current (excludes current maturities) — matches YF convention
        'LongTermDebtNoncurrent',
        'LongTermDebt',
        'LongTermDebtAndCapitalLeaseObligations',
        'LongTermNotesPayable',
        'LongTermDebtExcludingCurrentMaturities',
    ]),
    'Total Liabilities': ('BS', [
        'Liabilities',
    ]),
    'Stockholders Equity': ('BS', [
        'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
        'StockholdersEquity',
        'PartnersCapital',
        'MembersEquity',
    ]),

    # ── Cash Flow ─────────────────────────────────────────────────────────────
    'Operating Cash Flow': ('CF', [
        'NetCashProvidedByUsedInOperatingActivities',
        'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations',
    ]),
    'D&A (CF)': ('CF', [
        'DepreciationDepletionAndAmortization',
        'DepreciationAndAmortization',
        'OtherDepreciationAndAmortization',
    ]),
    'Stock Based Compensation': ('CF', [
        'ShareBasedCompensation',
        'AllocatedShareBasedCompensationExpense',
        'EmployeeBenefitsAndShareBasedCompensation',
        'StockBasedCompensation',
    ]),
    'CapEx': ('CF', [
        'PaymentsToAcquirePropertyPlantAndEquipment',
        'PaymentsForCapitalImprovements',
        # CapitalExpendituresIncurredButNotYetPaid removed: non-cash accrual, not a CF payment
        'PaymentsToAcquireProductiveAssets',
    ]),
    'Acquisitions': ('CF', [
        'PaymentsToAcquireBusinessesNetOfCashAcquired',
        'PaymentsToAcquireBusinessesGross',
        'PaymentsToAcquireBusinessesAndInterestInAffiliates',
    ]),
    'Investing Cash Flow': ('CF', [
        'NetCashProvidedByUsedInInvestingActivities',
        'NetCashProvidedByUsedInInvestingActivitiesContinuingOperations',
    ]),
    'Debt Issued': ('CF', [
        'ProceedsFromIssuanceOfLongTermDebt',
        'ProceedsFromDebtNetOfIssuanceCosts',
        'ProceedsFromLongTermLinesOfCredit',
        'ProceedsFromIssuanceOfDebt',
    ]),
    'Debt Repaid': ('CF', [
        'RepaymentsOfDebtAndCapitalLeaseObligations',
        'RepaymentsOfLongTermDebt',
        'RepaymentsOfDebt',
        'RepaymentsOfDebtMaturingInMoreThanThreeMonths',
        'RepaymentOfNotesPayable',
        'RepaymentsOfLongTermDebtAndCapitalSecurities',
    ]),
    'Dividends Paid': ('CF', [
        'PaymentsOfDividends',
        'PaymentsOfDividendsCommonStock',
        'PaymentsOfDividendsPreferredStockAndPreferenceStock',
        'PaymentsOfOrdinaryDividends',
    ]),
    'Share Repurchases': ('CF', [
        'PaymentsForRepurchaseOfCommonStock',
        'TreasuryStockValueAcquiredCostMethod',
        'StockRepurchasedAndRetiredDuringPeriodValue',
        'PaymentsForRepurchaseOfEquity',
    ]),
    'Financing Cash Flow': ('CF', [
        'NetCashProvidedByUsedInFinancingActivities',
        'NetCashProvidedByUsedInFinancingActivitiesContinuingOperations',
    ]),
}


# Concepts that must not be mapped to any metric, even if reachable via BFS.
# These are structural roll-up totals that would produce incorrect mappings.
BLOCKLIST: frozenset[str] = frozenset({
    # Equals Total Assets — using as Liabilities proxy breaks BS equation
    'LiabilitiesAndStockholdersEquity',
    # Abstract containers — organisational nodes, not reportable values
    # (also caught by Abstract-suffix check in build_metric_map.py)
    'StatementLineItems',
    'IncomeStatementAbstract',
    'BalanceSheetAbstract',
    'CashFlowStatementAbstract',

    # ── Wrong-context tags observed in practice ────────────────────────────────
    # Non-cash debt issuance cost write-off on refinancing — not interest expense
    'WriteOffOfDeferredDebtIssuanceCost',
    # FX transaction gain/loss — not operating revenue
    'ForeignCurrencyTransactionGainLossBeforeTax',
    'ForeignCurrencyTransactionGainLossRealized',
    'ForeignCurrencyTransactionGainLossUnrealized',
    # Debt maturity schedule disclosures — note footnotes, not BS balances
    'LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths',
    'LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo',
    'LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree',
    'LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour',
    'LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive',
    'LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive',
    # Gross intangibles (pre-amortization) — use net value tags only
    'IntangibleAssetsGrossExcludingGoodwill',
    'FiniteLivedIntangibleAssetsGross',

    # ── Lease-accounting tags that shadow real metrics ─────────────────────────
    # ROU asset amortization — ASC 842 lease entry, not cash D&A
    'FinanceLeaseRightOfUseAssetAmortization',
    # Single component of liabilities — GPI and others don't file Liabilities aggregate,
    # so BFS would map this component as a proxy, severely understating total liabilities
    'OperatingLeaseLiability',
    'OperatingLeaseLiabilityNoncurrent',
    'OperatingLeaseLiabilityCurrent',
    'FinanceLeaseLiability',

    # ── Income statement partial-component tags ────────────────────────────────
    # Equity method income is one IS line, not the full pretax income aggregate
    'IncomeLossFromEquityMethodInvestments',
    # Credit loss / loan-loss provision — finance-sector item, not COGS
    'ProvisionForLoanLeaseAndOtherLosses',
    'ProvisionForLoanAndLeaseLosses',
    # "Other" catch-all buckets — partial components that understate the true aggregate
    'OtherCostOfOperatingRevenue',
    'DirectCostsOfLeasedAndRentedPropertyOrEquipment',

    # ── Non-trade payables ─────────────────────────────────────────────────────
    # "Other" current AP sub-items or accrued expenses — not the full trade payables balance
    'AccountsPayableOtherCurrent',
    'AccountsPayableRelatedPartiesCurrent',
    # Accrued liabilities != trade accounts payable; different BS line items
    'AccruedLiabilitiesCurrent',

    # ── Balance sheet items that shadow aggregate statement totals ──────────────
    # Deferred tax liability is one BS component, not total liabilities
    'DeferredIncomeTaxLiabilitiesNet',
    'DeferredTaxLiabilitiesNoncurrent',

    # ── Debt cost / financing items that shadow D&A ────────────────────────────
    # Amortization of debt issuance costs — non-cash financing expense, not asset D&A
    'AmortizationOfFinancingCostsAndDiscounts',
    'AmortizationOfDebtDiscountPremium',
    'AmortizationOfFinancingCosts',

    # ── Provision / allowance tags that map via BFS to CoR or IS lines ─────────
    # Credit loss / loan-loss provisions — finance-sector or catch-all items, not COGS
    'ProvisionForLoanLossesExpensed',
    'ProvisionForOtherLosses',
    'ProvisionForDoubtfulAccounts',
    'AllowanceForLoanAndLeaseLossesProvisionForLoss',

    # ── Partial non-operating income items that shadow pretax income ───────────
    # These are single IS line items, not the full pretax income aggregate
    'NonoperatingIncomeExpense',
    'OtherNonoperatingIncomeExpense',

    # ── Tax reconciliation footnote disclosures — not IS line items ───────────
    'IncomeTaxReconciliationIncomeTaxExpenseBenefitAtFederalStatutoryIncomeTaxRate',
    'IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance',
    'IncomeTaxReconciliationOtherReconcilingItems',
    'IncomeTaxReconciliationStateAndLocalIncomeTaxes',
    # Deferred tax roll-forward disclosures — not current period income tax expense
    'ValuationAllowanceDeferredTaxAssetChangeInAmount',
    'DeferredTaxAssetsValuationAllowance',

    # ── Employee / pension items that shadow pretax income or liabilities ──────
    # Accrued compensation — current-period payroll accrual, not accounts payable
    'EmployeeRelatedLiabilitiesCurrent',
    # Defined benefit interest cost — pension note item, not pretax income
    'DefinedBenefitPlanInterestCost',
    'DefinedBenefitPlanNetPeriodicBenefitCost',
    # Deferred compensation plan liability — not total liabilities
    'DeferredCompensationLiabilityCurrentAndNoncurrent',

    # ── Government assistance / other one-time items ───────────────────────────
    # Government grant income — special-purpose income line, not pretax income aggregate
    'GovernmentAssistanceNonoperatingIncome',
    # Net investment income/expense — financing line, not pretax income aggregate
    'InvestmentIncomeInvestmentExpense',

    # ── Pension / defined benefit plan components ──────────────────────────────
    # These are pension note sub-items that feed into total pension expense,
    # not standalone pretax income or total liability concepts.
    'DefinedBenefitPlanExpectedReturnOnPlanAssets',
    'DefinedBenefitPlanServiceCost',
    'DefinedBenefitPlanAmortizationOfGainsLosses',
    'DefinedBenefitPlanAmortizationOfPriorServiceCostCredit',
    'DefinedBenefitPlanRecognizedNetGainLossDueToSettlements',
    'DefinedBenefitPlanNetPeriodicBenefitCostCredit',

    # ── Equity method investment payments — not business acquisitions ──────────
    # Minority-stake investments (<50%), distinct from business combinations
    'PaymentsToAcquireEquityMethodInvestments',
    'PaymentsToAcquireOtherInvestments',

    # ── Wrong-proxy depth-1 fallback tags ─────────────────────────────────────
    # Non-recurring goodwill impairment charge — BFS child of OperatingIncomeLoss,
    # but it is not a proxy for total operating income (e.g. NUE filed 0 impairment
    # while actual OperatingIncomeLoss was 6.2B)
    'GoodwillImpairmentLoss',
    # Insurance self-funding reserve — component of Total Liabilities, not the total
    # (e.g. BBY: EDGAR=164M vs YF=13B when Liabilities aggregate absent)
    'SelfInsuranceReserve',
    # Tax contingency accrual — footnote disclosure, not Total Liabilities aggregate
    # (e.g. HTO: EDGAR=4.5K vs YF=3.1B)
    'UnrecognizedTaxBenefits',
    # Single-item liability components — wrong Total Liabilities proxies that win
    # on magnitude when Liabilities and LiabilitiesNoncurrent are both absent.
    # (LiabilitiesNoncurrent is intentionally NOT blocked — needed for component-sum fallback)
    'AssetRetirementObligation',
    'OtherLiabilitiesNoncurrent',
    'RestructuringReserve',
    'SelfInsuranceReserveNoncurrent',   # non-current variant (e.g. BBY)
    'RegulatoryLiabilityNoncurrent',    # utility regulatory liability (e.g. HTO)
    # Intangible assets — contra-asset or future-period forecasts, not current balance
    # Accumulated amortization contra-asset — not the net intangible value
    # (e.g. BBY: EDGAR picked accumulated amortization instead of net intangibles)
    'FiniteLivedIntangibleAssetsAccumulatedAmortization',
    # Next-twelve-months amortization expense forecast — note disclosure, not BS balance
    # (e.g. BBY: 61M forecast picked vs 189M net balance)
    'FiniteLivedIntangibleAssetsAmortizationExpenseNextTwelveMonths',
    # Gross PP&E before accumulated depreciation — not the net value.
    # Pruning these stops BFS traversal through them, so their subtrees
    # (Land, LeaseholdImprovementsGross, MachineryAndEquipmentGross, etc.)
    # are not discovered as depth-2+ PP&E Net candidates.
    'PropertyPlantAndEquipmentGross',
    # Construction-in-progress — sub-component of gross PP&E, definitely not net
    # (e.g. PCG: EDGAR=4.4B vs YF=82.9B)
    'ConstructionInProgressGross',
    # The contra-asset (accumulated depreciation) — selecting this would give negative PP&E
    'AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment',
    # Future-period intangible amortization schedule disclosures — note footnotes, not BS balances
    # (e.g. BBY: YearThree forecast 21M picked vs 189M actual net intangible balance)
    'FiniteLivedIntangibleAssetsAmortizationExpenseYearTwo',
    'FiniteLivedIntangibleAssetsAmortizationExpenseYearThree',
    'FiniteLivedIntangibleAssetsAmortizationExpenseYearFour',
    'FiniteLivedIntangibleAssetsAmortizationExpenseYearFive',
    'FiniteLivedIntangibleAssetsAmortizationExpenseAfterYearFive',
    'FiniteLivedIntangibleAssetsAmortizationExpenseRemainderOfFiscalYear',
    # Deferred costs — prepaid/deferred contract costs, not current assets total
    # (e.g. TNL: DeferredCostsCurrent=58M picked vs AssetsCurrent=4,757M)
    'DeferredCostsCurrent',
    'DeferredCostsNoncurrent',
    # Net interest income/expense — can be negative for net borrowers, not pure interest income
    # (e.g. MO: InterestIncomeExpenseNet=-989M vs actual interest income 160M)
    'InterestIncomeExpenseNet',
    'InterestIncomeExpenseAfterProvisionForLoanLoss',
    # Finance lease interest only — partial component, not total interest expense
    # (e.g. KFY: FinanceLeaseInterestExpense=190K vs total InterestExpense=25.8M)
    'FinanceLeaseInterestExpense',
})


# Metric priority for conflict resolution: when two metrics can claim the same
# XBRL tag at equal depth, the metric that appears earlier here wins.
METRIC_PRIORITY: list[str] = [
    # IS (high-level totals first, then line items)
    'Revenue',
    'Gross Profit',
    'Operating Income',
    'Pretax Income',
    'Net Income',
    'Cost of Revenue',
    'R&D Expense',
    'SG&A Expense',
    'Depreciation & Amortization',
    'Interest Expense',
    'Interest Income',
    'Income Tax Expense',
    'EPS Basic',
    'EPS Diluted',
    'Shares Basic',
    'Shares Diluted',
    # BS
    'Total Assets',
    'Total Liabilities',
    'Stockholders Equity',
    'Current Assets',
    'Current Liabilities',
    'Cash',
    'Short Term Investments',
    'Accounts Receivable',
    'Inventory',
    'PP&E Net',
    'Goodwill',
    'Intangible Assets',
    'Accounts Payable',
    'Short Term Debt',
    'Long Term Debt',
    # CF
    'Operating Cash Flow',
    'Investing Cash Flow',
    'Financing Cash Flow',
    'CapEx',
    'Acquisitions',
    'D&A (CF)',
    'Stock Based Compensation',
    'Debt Issued',
    'Debt Repaid',
    'Dividends Paid',
    'Share Repurchases',
]
