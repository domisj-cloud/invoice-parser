# Output Format — EN 16931 (UBL 2.1)

The parser writes invoice output as **EN 16931-compliant UBL 2.1 XML**.

EN 16931 is the European semantic standard for electronic invoicing
(officially: *"Electronic invoicing — Semantic data model of the core
elements of an electronic invoice"*). It defines what an invoice
*means* — a list of business terms (BT codes) and business groups
(BG codes) — but leaves the wire syntax to two officially-permitted
bindings:

- **OASIS UBL 2.1** — Universal Business Language Invoice / CreditNote
- **UN/CEFACT CII** — Cross Industry Invoice

This parser implements the **UBL 2.1 binding**. Documents declare
conformance via:

```xml
<cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>
```

## Why EN 16931

| Driver | Detail |
| --- | --- |
| Regulatory | Mandatory for B2G in every EU member state; mandatory for B2B in France (2026), Germany (2027), Belgium, Poland and others rolling out |
| Reach | UBL 2.1 is supported by virtually every ERP, e-invoicing portal, and the PEPPOL network worldwide |
| Stability | UBL 2.1 has been schema-stable since 2013; the EN 16931 customization identifier has been stable since 2017 |
| Pivotability | PEPPOL BIS Billing 3.0, xRechnung, and most national EU profiles are **subsets** of EN 16931 — once the data is EN 16931-shaped, generating those variants is mostly adding extra code-list constraints |

## Output Shape

Commercial invoices (default) and receipts use the `<Invoice>` root.
Credit notes use the `<CreditNote>` root with different child names
(`CreditNoteLine` instead of `InvoiceLine`, `CreditedQuantity` instead
of `InvoicedQuantity`, `CreditNoteTypeCode` 381 instead of
`InvoiceTypeCode` 380).

Example commercial invoice:

```xml
<?xml version='1.0' encoding='utf-8'?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>
  <cbc:ID>88IB6AXP-0003</cbc:ID>
  <cbc:IssueDate>2026-05-08</cbc:IssueDate>
  <cbc:DueDate>2026-05-08</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>Anthropic, PBC</cbc:Name></cac:PartyName>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Anthropic, PBC</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>...</cac:AccountingCustomerParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">3.78</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">18.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">3.78</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>21</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">18.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">18.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">21.78</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">21.78</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">18.00</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Description>Claude Pro - May 8-Jun 8, 2026</cbc:Description>
      <cbc:Name>Claude Pro - May 8-Jun 8, 2026</cbc:Name>
      <cac:ClassifiedTaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>21</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="EUR">18.00</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>
</Invoice>
```

## Field Mapping

EN 16931 identifies fields by **BT** (Business Term) and **BG** (Business
Group) codes. The mapping from the parser's internal model to UBL is:

| UBL element | EN 16931 | Source (internal model) |
| --- | --- | --- |
| `cbc:CustomizationID` | BT-24 | Fixed: `urn:cen.eu:en16931:2017` |
| `cbc:ID` | BT-1 | `Invoice.number` |
| `cbc:IssueDate` | BT-2 | `Invoice.date` (normalized to ISO 8601) |
| `cbc:DueDate` | BT-9 | `Invoice.due_date` (normalized to ISO 8601, optional) |
| `cbc:InvoiceTypeCode` / `cbc:CreditNoteTypeCode` | BT-3 | `380` for invoices/receipts, `381` for credit notes |
| `cbc:DocumentCurrencyCode` | BT-5 | `Invoice.currency` (ISO 4217) |
| `AccountingSupplierParty/.../PartyName/Name` | BT-28 | `Invoice.seller.name` |
| `AccountingSupplierParty/.../PartyTaxScheme/CompanyID` | BT-31 | `Invoice.seller.vat` (omitted if absent) |
| `AccountingSupplierParty/.../PartyLegalEntity/RegistrationName` | BT-27 | `Invoice.seller.name` |
| `AccountingSupplierParty/.../PartyLegalEntity/CompanyID` | BT-30 | `Invoice.seller.tax_id` (optional) |
| `AccountingCustomerParty/...` | BG-7 | `Invoice.buyer` (mirrors supplier mapping) |
| `TaxTotal/TaxAmount` | BT-110 | `Invoice.totals.tax` if non-zero, else summed from lines |
| `TaxTotal/TaxSubtotal` (one per rate) | BG-23 | Aggregated from lines by `vat_rate` |
| `TaxSubtotal/TaxCategory/ID` | BT-118 | `S` for non-zero rate, `Z` for 0% |
| `TaxSubtotal/TaxCategory/Percent` | BT-119 | line `vat_rate` |
| `LegalMonetaryTotal/LineExtensionAmount` | BT-106 | `Invoice.totals.subtotal` |
| `LegalMonetaryTotal/TaxExclusiveAmount` | BT-109 | `Invoice.totals.subtotal` |
| `LegalMonetaryTotal/TaxInclusiveAmount` | BT-112 | `Invoice.totals.total` |
| `LegalMonetaryTotal/PayableAmount` | BT-115 | `Invoice.totals.total` |
| `InvoiceLine/ID` | BT-126 | 1-based index |
| `InvoiceLine/InvoicedQuantity` | BT-129 | `InvoiceLine.quantity`, `unitCode="C62"` |
| `InvoiceLine/LineExtensionAmount` | BT-131 | `InvoiceLine.line_total` |
| `InvoiceLine/Item/Description` | BT-154 | `InvoiceLine.description` |
| `InvoiceLine/Item/Name` | BT-153 | `InvoiceLine.description` |
| `InvoiceLine/Item/SellersItemIdentification/ID` | BT-155 | `InvoiceLine.sku` (omitted if absent) |
| `InvoiceLine/Item/ClassifiedTaxCategory` | BG-30 | Derived from `InvoiceLine.vat_rate` |
| `InvoiceLine/Price/PriceAmount` | BT-146 | `InvoiceLine.unit_price` |

## Code Lists

The PoC uses fixed values for code-list fields. Production would derive
these from the source data when available.

| List | Field | Value used by PoC |
| --- | --- | --- |
| UNCL5305 (tax category) | `cbc:ID` in TaxCategory / ClassifiedTaxCategory | `S` for rate > 0, `Z` for rate == 0 |
| UN/ECE Rec 20 (unit of measure) | `unitCode` on InvoicedQuantity / CreditedQuantity | `C62` ("one", dimensionless) |
| UNCL1001 (invoice type code) | `cbc:InvoiceTypeCode` | `380` (commercial invoice) |
| UNCL1001 (credit note type code) | `cbc:CreditNoteTypeCode` | `381` (credit note) |
| ISO 4217 (currency) | `currencyID` attribute on monetary amounts | Pass-through from `Invoice.currency` |
| ISO 8601 (dates) | `cbc:IssueDate`, `cbc:DueDate` | Normalized in the serializer from common date strings |

## Decimal Rules

Monetary amounts are quantized to **2 decimal places** with
`ROUND_HALF_UP` per EN 16931 BR-DEC-* rules. The `currencyID` attribute
on every monetary element matches `Invoice.currency`.

Quantities and percentages keep their natural precision (integers stay
integer; fractions print their non-zero digits).

## Date Normalization

EN 16931 BT-2 / BT-9 / BT-72 require ISO 8601 (`YYYY-MM-DD`). The
serializer accepts several common formats from upstream PDFs and
normalizes them:

| Input | Output |
| --- | --- |
| `2026-05-08` | `2026-05-08` (already ISO) |
| `2026/05/08` | `2026-05-08` |
| `May 8, 2026` | `2026-05-08` |
| `8 May 2026` | `2026-05-08` |
| `08-May-2026` | `2026-05-08` |

Ambiguous formats like `08/05/2026` (could be DD/MM/YYYY *or*
MM/DD/YYYY) are **not** auto-guessed; the original string is passed
through unchanged so a downstream EN 16931 Schematron validator flags
the issue loudly rather than silently committing to one interpretation.

## Known Limitations of This PoC

The output is **structurally** EN 16931 UBL 2.1 — valid against the UBL
2.1 XSD with the correct CustomizationID. Full **semantic** conformance
(passing the EN 16931 Schematron rule set) depends on data the PoC
parser does not yet extract from PDFs:

| Missing today | EN 16931 reference | Impact |
| --- | --- | --- |
| Seller / buyer postal address | BG-5 / BG-8 (BT-35, BT-50, country code BT-40 / BT-55) | Schematron BR-08 / BR-31 may flag |
| Payment terms, due date detail | BT-20 / BT-9 (if absent) | Some Schematron rules expect these |
| Payment means (bank account, etc.) | BG-16 | Optional in core; required by some profiles like PEPPOL BIS |
| Tax category exemption reasons (E, K, O, etc.) | BT-120 / BT-121 | Only matters for non-Standard categories; we always emit `S` or `Z` |

For a "production-grade" deployment, the parser would need to extract
address fields, payment means, and validate against the official
[EN 16931 Schematron rules](https://github.com/ConnectingEurope/eInvoicing-EN16931).
A `validate` step that runs the Schematron rules against generated
output and routes non-conformant invoices to `inv-error` is a natural
next step.

## Pivoting to Adjacent Standards

Once the data is EN 16931-shaped, generating profile variants is
mostly metadata work:

| Target standard | Change required |
| --- | --- |
| **PEPPOL BIS Billing 3.0** | Add `ProfileID = urn:fdc:peppol.eu:2017:poacc:billing:01:1.0`, enforce stricter required-field set, add code-list validation |
| **xRechnung** (Germany) | Same UBL shape; CustomizationID `urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0`; required Leitweg-ID (BT-10) |
| **Factur-X / ZUGFeRD** (DE/FR) | Switch syntax from UBL to UN/CEFACT CII, embed XML inside PDF/A-3 |
| **FatturaPA** (Italy) | Different schema entirely; the parser would need a second serializer |

None of those are wired up in this PoC, but the internal `Invoice`
model has the data shape needed for all of them.
