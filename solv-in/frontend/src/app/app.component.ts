import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpClientModule, HttpHeaders, HttpParams } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { environment } from '../environments/environment';

interface User { full_name: string; email: string; role: string; is_active?: boolean; }
interface Claim {
  claim_id: string; customer_name: string; email?: string; phone?: string;
  policy_number: string; claim_type: 'Property' | 'Casualty'; property_occupancy?: string | null;
  casualty_segment?: 'Commercial' | null; business_name?: string | null; industry?: string | null;
  commercial_liability_type?: string | null; incident_date: string;
  incident_description: string; loss_location: string; country_code: string; region?: string; locality?: string; postal_code?: string; estimated_loss?: number | null;
  currency: string; status: string; created_at?: string; updated_at?: string;
}
interface ClaimDocument { file_id: string; claim_id: string; filename: string; content_type: string; size: number; uploaded_at?: string; uploaded_by?: string; }
interface DamageAssessment {
  assessment_id: string; claim_id?: string | null; damage_category: string;
  affected_areas: string[]; visible_observations: string[]; severity: string;
  confidence: string; possible_cause: string; recommended_next_steps: string[];
  limitations: string[]; repair_cost_estimate: number | null; status: string; created_at: string;
  review_history?: { status: string; notes: string; reviewed_at: string; reviewed_by: string; reviewer_role: string }[];
}
interface FinancialEstimate { estimate_id: string; claim_id: string; currency: string; gross_repair_cost: string; depreciation: string; net_repair_cost: string; total_fees_and_charges: string; total_tax: string; interest: { amount: string }; deductible: string; total_before_deductible: string; preliminary_net_estimate: string; status: string; created_at: string; pricing_source: string; state_or_jurisdiction: string; taxes: any[]; line_items: any[]; tax_mode?: string; tax_profile_status?: string; tax_rule_warning?: string | null; tax_rule_ids?: string[]; review_status?: string; review_history?: any[]; }
interface EmailConnectorStatus { gmail: { configured: boolean; watch_active: boolean; watch_expiration?: string | null }; microsoft365: { configured: boolean; subscription_active: boolean; subscription_expiration?: string | null }; note: string; }
interface EmailIntakeItem { intake_id: string; sender_email: string; subject: string; received_at: string; suggested_claim_type: string; confirmed_claim_type?: string; classification_reason: string; missing_fields: string[]; review_status: string; extracted_fields: Record<string, string | null>; claim_id?: string; }
interface ClaimDraft {
  customer_name: string; email: string; phone: string; policy_number: string;
  claim_type: 'Property' | 'Casualty'; property_occupancy: string | null;
  casualty_segment: 'Commercial' | null; business_name: string; industry: string;
  commercial_liability_type: string | null; incident_date: string; incident_description: string;
  loss_location: string; country_code: string; region: string; locality: string; postal_code: string; estimated_loss: number | null; currency: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, HttpClientModule, FormsModule],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnInit {
  private http = inject(HttpClient);
  isAuthenticated = false;
  currentUser: User | null = null;
  loginEmail = '';
  loginPassword = '';
  loginBusy = false;
  activePage = 'Dashboard';
  apiStatus = 'Checking API…';
  loadingClaims = false;
  savingClaim = false;
  errorMessage = '';
  successMessage = '';
  searchTerm = '';
  typeFilter = '';
  statusFilter = '';
  claims: Claim[] = [];
  selectedClaim: Claim | null = null;
  loadingClaimDetails = false;\n  claimDocuments: ClaimDocument[] = [];\n  selectedClaimDocument: File | null = null;\n  documentUploadBusy = false;\n  documentsLoading = false;
  users: User[] = [];
  roles: string[] = [];
  userBusy = false;
  selectedDamageImage: File | null = null;
  damageImagePreview = '';
  damageClaimId = '';
  damageBusy = false;
  damageAssessment: DamageAssessment | null = null;
  damageAssessments: DamageAssessment[] = [];
  damageReviewNotes = '';
  damageReviewBusyId = '';
  estimateClaimId = '';
  estimateBusy = false;
  financialEstimate: FinancialEstimate | null = null;
  financialEstimates: FinancialEstimate[] = [];
  financialReviewNotes = '';
  financialReviewBusyId = '';
  policies: any[] = [];
  policyDraft: any = this.emptyPolicyDraft();
  policyBusy = false;
  coverageClaimId = '';
  coverageLossCategory = '';
  coverageClaimedAmount: number | null = null;
  coverageBusy = false;
  coverageAssessment: any = null;
  coverageAssessments: any[] = [];
  taxRules: any[] = [];
  taxRuleDraft: any = this.emptyTaxRuleDraft();
  taxRulesBusy = false;
  taxRuleCountryFilter = '';
  applicableTaxRules: any = null;
  estimateDraft: any = this.emptyEstimateDraft();
  newUser = { full_name: '', email: '', password: '', role: 'Claims Manager' };
  totalClaims = 0;
  newClaims = 0;
  investigatingClaims = 0;
  pendingClaims = 0;
  closedClaims = 0;
  unclassifiedLegacyCasualtyClaims = 0;
  analyticsData: any = null;
  emailIntakeItems: EmailIntakeItem[] = [];
  emailIntakeBusy = false;
  emailConnectorStatus: EmailConnectorStatus | null = null;
  emailConnectorBusy = false;
  selectedEmailIntakeId = '';
  analyticsBusy = false;
  analyticsFromDate = '';
  analyticsToDate = '';
  analyticsCountry = '';
  analyticsCurrency = '';
  analyticsPortfolio = 'All';
  readonly navigation = ['Dashboard', 'Claims', 'Email FNOL', 'New FNOL', 'AI Damage Analysis', 'Policy Coverage', 'Financial Estimate', 'Tax Rules', 'Reports', 'Users', 'Settings'];
  readonly statuses = ['FNOL Received', 'Under Review', 'Investigation', 'Assessment', 'Pending Approval', 'Approved', 'Closed'];
  draft: ClaimDraft = this.emptyDraft();

  ngOnInit(): void {
    const token = sessionStorage.getItem('solvin_access_token');
    if (token) {
      this.http.get<User>(`${environment.apiBaseUrl}/auth/me`, { headers: this.authHeaders() }).subscribe({
        next: user => { this.currentUser = user; this.isAuthenticated = true; this.afterLogin(); },
        error: () => this.logout(false)
      });
    } else {
      this.checkApi();
    }
  }

  private authHeaders(): HttpHeaders {
    const token = sessionStorage.getItem('solvin_access_token') || '';
    return new HttpHeaders({ Authorization: `Bearer ${token}` });
  }

  checkApi(): void {
    this.http.get<{status: string}>(`${environment.apiBaseUrl}/health`).subscribe({
      next: result => this.apiStatus = `API ${result.status}`,
      error: () => this.apiStatus = 'API unavailable – start the FastAPI server'
    });
  }

  login(): void {
    this.errorMessage = '';
    this.loginBusy = true;
    this.http.post<{access_token: string; user: User}>(`${environment.apiBaseUrl}/auth/login`, {
      email: this.loginEmail, password: this.loginPassword
    }).subscribe({
      next: result => {
        sessionStorage.setItem('solvin_access_token', result.access_token);
        this.currentUser = result.user;
        this.isAuthenticated = true;
        this.loginPassword = '';
        this.loginBusy = false;
        this.afterLogin();
      },
      error: error => {
        this.loginBusy = false;
        this.errorMessage = error.error?.detail || 'Unable to sign in. Check the API and your credentials.';
      }
    });
  }

  private afterLogin(): void {
    this.apiStatus = 'Signed in';
    this.loadDashboard();
    this.loadClaims();
    if (this.isAdmin()) this.loadUsers();
  }

  logout(showMessage = true): void {
    sessionStorage.removeItem('solvin_access_token');
    this.currentUser = null;
    this.isAuthenticated = false;
    this.activePage = 'Dashboard';
    this.claims = [];
    this.users = [];
    this.errorMessage = '';
    this.successMessage = showMessage ? 'You have signed out.' : '';
    this.checkApi();
  }

  isAdmin(): boolean {
    return this.currentUser?.role === 'Super Administrator';
  }

  canManageTaxRules(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator'].includes(this.currentUser?.role || '');
  }

  canManagePolicies(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator'].includes(this.currentUser?.role || '');
  }

  canViewPolicyCoverage(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator', 'Claims Manager', 'Property Adjuster', 'Casualty Adjuster', 'Surveyor', 'Actuarial Analyst', 'Approver', 'Auditor'].includes(this.currentUser?.role || '');
  }

  latestFinancialReview(estimate: FinancialEstimate): any | null {
    const history = estimate.review_history;
    return history?.length ? history[history.length - 1] : null;
  }

  canAnalyseDamage(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator', 'Claims Manager', 'Property Adjuster', 'Casualty Adjuster', 'Surveyor', 'Actuarial Analyst', 'Approver'].includes(this.currentUser?.role || '');
  }

  emptyPolicyDraft(): any {
    return {
      policy_number: '', insured_name: '', claim_type: 'Property', country_code: '', currency: '',
      effective_from: '', effective_to: '', per_occurrence_limit: 0, aggregate_limit: null,
      aggregate_used: 0, deductible: 0, excluded_loss_categories: '', covered_loss_categories: '',
      endorsements: '', policy_wording_reference: '', notes: ''
    };
  }

  loadPolicies(): void {
    this.http.get<{items: any[]; count: number}>(`${environment.apiBaseUrl}/policies`, { headers: this.authHeaders() }).subscribe({
      next: result => this.policies = result.items,
      error: error => this.handleApiError(error)
    });
  }

  createPolicy(): void {
    const d = this.policyDraft;
    if (!d.policy_number.trim() || !d.insured_name.trim() || !d.effective_from || !d.effective_to ||
        !/^[A-Z]{2}$/i.test(d.country_code) || !/^[A-Z]{3}$/i.test(d.currency) ||
        !d.policy_wording_reference.trim() || Number(d.per_occurrence_limit) <= 0) {
      this.errorMessage = 'Complete the required policy details, valid country/currency codes, policy dates, limit and wording reference.';
      return;
    }
    const splitValues = (value: string) => value.split(',').map(x => x.trim()).filter(Boolean);
    const body = {
      ...d, country_code: d.country_code.toUpperCase(), currency: d.currency.toUpperCase(),
      aggregate_limit: d.aggregate_limit === '' || d.aggregate_limit === null ? null : Number(d.aggregate_limit),
      per_occurrence_limit: Number(d.per_occurrence_limit), aggregate_used: Number(d.aggregate_used || 0),
      deductible: Number(d.deductible || 0),
      excluded_loss_categories: splitValues(d.excluded_loss_categories),
      covered_loss_categories: splitValues(d.covered_loss_categories),
      endorsements: splitValues(d.endorsements)
    };
    this.policyBusy = true;
    this.http.post<any>(`${environment.apiBaseUrl}/policies`, body, { headers: this.authHeaders() }).subscribe({
      next: () => {
        this.policyBusy = false;
        this.successMessage = 'Policy record saved. Coverage checks will remain preliminary.';
        this.policyDraft = this.emptyPolicyDraft();
        this.loadPolicies();
      },
      error: error => { this.policyBusy = false; this.handleApiError(error); }
    });
  }

  assessCoverage(): void {
    const id = this.coverageClaimId.trim();
    if (!id || !this.coverageLossCategory.trim()) {
      this.errorMessage = 'Enter a Claim ID and the reported loss category.';
      return;
    }
    this.coverageBusy = true;
    this.coverageAssessment = null;
    this.http.post<any>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/coverage-assessments`, {
      loss_category: this.coverageLossCategory.trim(),
      claimed_amount: this.coverageClaimedAmount,
      notes: ''
    }, { headers: this.authHeaders() }).subscribe({
      next: result => {
        this.coverageBusy = false;
        this.coverageAssessment = result;
        this.loadCoverageAssessments();
      },
      error: error => { this.coverageBusy = false; this.handleApiError(error); }
    });
  }

  loadCoverageAssessments(): void {
    const id = this.coverageClaimId.trim();
    if (!id) return;
    this.http.get<{items: any[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/coverage-assessments`, { headers: this.authHeaders() }).subscribe({
      next: result => this.coverageAssessments = result.items,
      error: error => this.handleApiError(error)
    });
  }

  emptyTaxRuleDraft(): any {
    return { country_code: '', currency: '', admin_area: '', region: '', locality: '', tax_name: '', tax_type: 'Sales tax', rate_percent: 0, calculation_order: 100, applies_to: 'depreciated_repair_subtotal', compound_on_prior_taxes: false, tax_point_type: 'Tax point date', effective_from: new Date().toISOString().slice(0, 10), effective_to: '', authority_name: '', authority_reference: '', source_url: '', notes: '' };
  }

  loadTaxRules(): void {
    let params = new HttpParams();
    if (this.taxRuleCountryFilter.trim()) params = params.set('country_code', this.taxRuleCountryFilter.trim().toUpperCase());
    this.http.get<{items: any[]; count: number}>(`${environment.apiBaseUrl}/tax-rules`, { params, headers: this.authHeaders() }).subscribe({ next: result => this.taxRules = result.items, error: error => this.handleApiError(error) });
  }

  saveTaxRule(): void {
    const d = this.taxRuleDraft;
    if (!/^[A-Z]{2}$/i.test(d.country_code) || !/^[A-Z]{3}$/i.test(d.currency) || !d.tax_name.trim() || !d.effective_from || !d.authority_name.trim() || !d.authority_reference.trim()) { this.errorMessage = 'Enter valid country/currency codes, tax name, jurisdiction, effective date and official authority reference.'; return; }
    this.taxRulesBusy = true; this.errorMessage = '';
    const body = { ...d, country_code: d.country_code.toUpperCase(), currency: d.currency.toUpperCase(), effective_to: d.effective_to || null, source_url: d.source_url || null, rate_percent: Number(d.rate_percent) };
    this.http.post<any>(`${environment.apiBaseUrl}/tax-rules`, body, { headers: this.authHeaders() }).subscribe({ next: () => { this.taxRulesBusy = false; this.successMessage = 'Tax rule saved as Draft. An authorised administrator must verify and activate it.'; this.taxRuleDraft = this.emptyTaxRuleDraft(); this.loadTaxRules(); }, error: error => { this.taxRulesBusy = false; this.handleApiError(error); } });
  }

  setTaxRuleStatus(rule: any, status: 'Active' | 'Inactive'): void {
    const note = status === 'Active' ? 'Verified against the cited authority by authorised administrator.' : 'Deactivated by authorised administrator.';
    this.http.patch<any>(`${environment.apiBaseUrl}/tax-rules/${encodeURIComponent(rule.rule_id)}/status`, { status, verification_note: note }, { headers: this.authHeaders() }).subscribe({ next: () => { this.successMessage = `Tax rule ${status.toLowerCase()}.`; this.loadTaxRules(); }, error: error => this.handleApiError(error) });
  }

  resetTaxRuleConfirmation(): void {
    this.estimateDraft.confirm_automatic_tax_application = false;
    this.applicableTaxRules = null;
  }

  lookupApplicableTaxRules(): void {
    const id = this.estimateClaimId.trim();
    if (!id || !this.estimateDraft.tax_point_date) { this.errorMessage = 'Enter a Claim ID and tax point date.'; return; }
    this.estimateDraft.confirm_automatic_tax_application = false;
    this.applicableTaxRules = null;
    const params = new HttpParams().set('tax_point_date', this.estimateDraft.tax_point_date);
    this.http.get<any>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/applicable-tax-rules`, { params, headers: this.authHeaders() }).subscribe({ next: result => this.applicableTaxRules = result, error: error => this.handleApiError(error) });
  }

  emptyEstimateDraft(): any {
    return {
      description: '', quantity: 1, unit: 'item', unit_rate: 0, depreciation_rate: 0, taxable: true, tax_mode: 'automatic',
      currency: '', currency_minor_units: 2, country_code: '', state_or_jurisdiction: '', region: '', locality: '', postal_code: '',
      loss_date: new Date().toISOString().slice(0, 10), tax_point_date: new Date().toISOString().slice(0, 10),
      taxes: [], charges: [], deductible: 0, interest_applicable: false,
      interest_rate: null, interest_principal: null, interest_days: null, interest_basis: 'simple_actual_365',
      interest_authority: '', pricing_source: '', pricing_effective_date: new Date().toISOString().slice(0, 10), notes: ''
    };
  }

  addTaxComponent(): void {
    if (this.estimateDraft.taxes.length >= 20) return;
    this.estimateDraft.taxes.push({
      name: '', tax_type: 'Other', jurisdiction: '', rate_percent: 0,
      applies_to: 'depreciated_repair_subtotal', compound_on_prior_taxes: false,
      authority_reference: '', effective_from: '', effective_to: ''
    });
  }

  removeTaxComponent(index: number): void {
    this.estimateDraft.taxes.splice(index, 1);
  }

  addCharge(): void {
    if (this.estimateDraft.charges.length >= 50) return;
    this.estimateDraft.charges.push({ name: '', amount: 0, charge_type: 'fee', taxable: false, authority_reference: '' });
  }

  removeCharge(index: number): void {
    this.estimateDraft.charges.splice(index, 1);
  }

  emptyDraft(): ClaimDraft {
    return {
      customer_name: '', email: '', phone: '', policy_number: '',
      claim_type: 'Property', property_occupancy: 'Residential', casualty_segment: null,
      business_name: '', industry: '', commercial_liability_type: null,
      incident_date: '', incident_description: '',
      loss_location: '', country_code: '', region: '', locality: '', postal_code: '', estimated_loss: null, currency: ''
    };
  }

  onClaimTypeChange(): void {
    if (this.draft.claim_type === 'Casualty') {
      this.draft.property_occupancy = null;
      this.draft.casualty_segment = 'Commercial';
      this.draft.business_name = '';
      this.draft.industry = '';
      this.draft.commercial_liability_type = 'Commercial General Liability';
    } else {
      this.draft.property_occupancy = 'Residential';
      this.draft.casualty_segment = null;
      this.draft.business_name = '';
      this.draft.industry = '';
      this.draft.commercial_liability_type = null;
    }
  }

  selectPage(page: string): void {
    if (page === 'Users' && !this.isAdmin()) return;
    this.activePage = page;
    this.errorMessage = '';
    this.successMessage = '';
    if (page === 'Claims') this.loadClaims();
    if (page === 'Email FNOL') { this.loadEmailIntake(); this.loadEmailConnectorStatus(); }
    if (page === 'AI Damage Analysis' && this.damageClaimId.trim()) this.loadDamageAssessments();
    if (page === 'Users') this.loadUsers();
    if (page === 'Tax Rules' && this.canManageTaxRules()) this.loadTaxRules();
    if (page === 'Policy Coverage') this.loadPolicies();
    if (page === 'Reports') this.loadClaimsAnalytics();
  }

  loadDashboard(): void {
    this.http.get<Record<string, any>>(`${environment.apiBaseUrl}/dashboard/summary`, { headers: this.authHeaders() }).subscribe({
      next: data => {
        this.totalClaims = data['total_claims'] ?? 0;
        this.newClaims = data['new_claims'] ?? 0;
        this.investigatingClaims = data['under_investigation'] ?? 0;
        this.pendingClaims = data['pending_approval'] ?? 0;
        this.closedClaims = data['settled_claims'] ?? 0;
        this.unclassifiedLegacyCasualtyClaims = data['unclassified_legacy_casualty_claims'] ?? 0;
      },
      error: error => this.handleApiError(error)
    });
  }

  loadClaimsAnalytics(): void {
    this.analyticsBusy = true;
    let params = new HttpParams().set('portfolio', this.analyticsPortfolio);
    if (this.analyticsFromDate) params = params.set('from_date', this.analyticsFromDate);
    if (this.analyticsToDate) params = params.set('to_date', this.analyticsToDate);
    if (this.analyticsCountry.trim()) params = params.set('country_code', this.analyticsCountry.trim().toUpperCase());
    if (this.analyticsCurrency.trim()) params = params.set('currency', this.analyticsCurrency.trim().toUpperCase());
    this.http.get<any>(`${environment.apiBaseUrl}/reports/claims-analytics`, {
      params, headers: this.authHeaders()
    }).subscribe({
      next: data => { this.analyticsData = data; this.analyticsBusy = false; },
      error: error => { this.analyticsBusy = false; this.handleApiError(error); }
    });
  }

  exportClaimsAnalytics(): void {
    let params = new HttpParams().set('portfolio', this.analyticsPortfolio);
    if (this.analyticsFromDate) params = params.set('from_date', this.analyticsFromDate);
    if (this.analyticsToDate) params = params.set('to_date', this.analyticsToDate);
    if (this.analyticsCountry.trim()) params = params.set('country_code', this.analyticsCountry.trim().toUpperCase());
    if (this.analyticsCurrency.trim()) params = params.set('currency', this.analyticsCurrency.trim().toUpperCase());
    this.http.get(`${environment.apiBaseUrl}/reports/claims-analytics.csv`, {
      params, headers: this.authHeaders(), responseType: 'blob'
    }).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'solv-in-claims-analytics.csv';
        anchor.click();
        URL.revokeObjectURL(url);
      },
      error: error => this.handleApiError(error)
    });
  }

  loadClaims(): void {
    this.loadingClaims = true;
    let params = new HttpParams();
    if (this.searchTerm.trim()) params = params.set('search', this.searchTerm.trim());
    if (this.typeFilter) params = params.set('claim_type', this.typeFilter);
    if (this.statusFilter) params = params.set('status', this.statusFilter);
    this.http.get<{items: Claim[]; count: number}>(`${environment.apiBaseUrl}/claims`, { params, headers: this.authHeaders() }).subscribe({
      next: result => { this.claims = result.items; this.loadingClaims = false; },
      error: error => { this.loadingClaims = false; this.handleApiError(error); this.claims = []; }
    });
  }

  openClaimDetails(claim: Claim): void {
    this.errorMessage = '';
    this.successMessage = '';
    this.loadingClaimDetails = true;
    this.selectedClaim = null;
    this.activePage = 'Claim Details';
    this.http.get<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.selectedClaim = result; this.loadingClaimDetails = false; this.loadClaimDocuments(); },
      error: error => { this.loadingClaimDetails = false; this.handleApiError(error); }
    });
  }

  loadClaimDocuments(): void {
    const claimId = this.selectedClaim?.claim_id;
    if (!claimId) return;
    this.documentsLoading = true;
    this.http.get<{items: ClaimDocument[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}/documents`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.claimDocuments = result.items; this.documentsLoading = false; },
      error: error => { this.documentsLoading = false; this.handleApiError(error); }
    });
  }

  onClaimDocumentSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] || null;
    this.selectedClaimDocument = null;
    if (!file) return;
    const allowed = ['application/pdf', 'image/jpeg', 'image/png', 'image/webp'];
    if (!allowed.includes(file.type)) {
      this.errorMessage = 'Choose a PDF, JPG, JPEG, PNG or WEBP file.';
      input.value = '';
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      this.errorMessage = 'Each file must be 15 MB or smaller.';
      input.value = '';
      return;
    }
    this.errorMessage = '';
    this.selectedClaimDocument = file;
  }

  uploadClaimDocument(): void {
    if (!this.selectedClaim || !this.selectedClaimDocument) {
      this.errorMessage = 'Choose a document before uploading.';
      return;
    }
    this.documentUploadBusy = true;
    this.errorMessage = '';
    const body = new FormData();
    body.append('file', this.selectedClaimDocument);
    this.http.post<ClaimDocument>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(this.selectedClaim.claim_id)}/documents`, body, { headers: this.authHeaders() }).subscribe({
      next: result => {
        this.documentUploadBusy = false;
        this.selectedClaimDocument = null;
        this.successMessage = `${result.filename} uploaded successfully.`;
        this.loadClaimDocuments();
      },
      error: error => { this.documentUploadBusy = false; this.handleApiError(error); }
    });
  }

  downloadClaimDocument(document: ClaimDocument): void {
    const claimId = encodeURIComponent(document.claim_id);
    const fileId = encodeURIComponent(document.file_id);
    this.http.get(`${environment.apiBaseUrl}/claims/${claimId}/documents/${fileId}/download`, {
      headers: this.authHeaders(), responseType: 'blob'
    }).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const link = window.document.createElement('a');
        link.href = url;
        link.download = document.filename;
        link.click();
        URL.revokeObjectURL(url);
      },
      error: error => this.handleApiError(error)
    });
  }

  backToClaims(): void {
    this.selectedClaim = null;
    this.selectPage('Claims');
  }

  submitFnol(): void {
    this.errorMessage = '';
    this.successMessage = '';
    if (!this.draft.customer_name.trim() || !this.draft.policy_number.trim() ||
        !this.draft.incident_date || !this.draft.incident_description.trim() ||
        !this.draft.loss_location.trim() || !this.draft.country_code.trim() || !/^[A-Z]{3}$/i.test(this.draft.currency.trim()) ||
        (this.draft.claim_type === 'Casualty' && (!this.draft.business_name.trim() || !this.draft.industry.trim() || !this.draft.commercial_liability_type))) {
      this.errorMessage = 'Complete all required fields before submitting the FNOL.';
      return;
    }
    this.savingClaim = true;
    const url = this.selectedEmailIntakeId
      ? `${environment.apiBaseUrl}/email-intake/${encodeURIComponent(this.selectedEmailIntakeId)}/create-claim`
      : `${environment.apiBaseUrl}/claims`;
    this.http.post<Claim>(url, this.draft, { headers: this.authHeaders() }).subscribe({
      next: claim => {
        this.savingClaim = false;
        this.successMessage = `Claim ${claim.claim_id} was created successfully.`;
        this.draft = this.emptyDraft();
        this.selectedEmailIntakeId = '';
        this.loadClaims();
        this.loadDashboard();
      },
      error: error => { this.savingClaim = false; this.handleApiError(error); }
    });
  }



  canManageEmailConnectors(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator'].includes(this.currentUser?.role || '');
  }

  loadEmailConnectorStatus(): void {
    this.http.get<EmailConnectorStatus>(`${environment.apiBaseUrl}/email-connectors/status`, { headers: this.authHeaders() }).subscribe({
      next: status => this.emailConnectorStatus = status,
      error: error => this.handleApiError(error)
    });
  }

  startGmailConnector(): void {
    this.emailConnectorBusy = true;
    this.http.post<any>(`${environment.apiBaseUrl}/email-connectors/gmail/watch`, {}, { headers: this.authHeaders() }).subscribe({
      next: result => { this.emailConnectorBusy = false; this.successMessage = 'Gmail watch started. Configure the Google Pub/Sub push subscription to deliver notifications to Solv-In.'; this.loadEmailConnectorStatus(); },
      error: error => { this.emailConnectorBusy = false; this.handleApiError(error); }
    });
  }

  startMicrosoftConnector(): void {
    this.emailConnectorBusy = true;
    this.http.post<any>(`${environment.apiBaseUrl}/email-connectors/microsoft365/subscribe`, {}, { headers: this.authHeaders() }).subscribe({
      next: result => { this.emailConnectorBusy = false; this.successMessage = 'Microsoft 365 inbox subscription created.'; this.loadEmailConnectorStatus(); },
      error: error => { this.emailConnectorBusy = false; this.handleApiError(error); }
    });
  }

  loadEmailIntake(): void {
    this.emailIntakeBusy = true;
    this.http.get<{items: EmailIntakeItem[]; count: number}>(`${environment.apiBaseUrl}/email-intake`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.emailIntakeItems = result.items; this.emailIntakeBusy = false; },
      error: error => { this.emailIntakeBusy = false; this.handleApiError(error); }
    });
  }

  reviewEmailIntake(item: EmailIntakeItem, confirmedType: 'Property' | 'Commercial Casualty' | null, status: 'Reviewed' | 'Rejected'): void {
    if (status === 'Reviewed' && !confirmedType) {
      this.errorMessage = 'Confirm whether this is a Property or Commercial Casualty claim.';
      return;
    }
    const notes = status === 'Rejected' ? window.prompt('Enter the reason for rejecting this email intake:') : '';
    if (status === 'Rejected' && !notes?.trim()) return;
    this.http.patch<EmailIntakeItem>(`${environment.apiBaseUrl}/email-intake/${encodeURIComponent(item.intake_id)}/review`,
      { status, notes: notes || '', confirmed_claim_type: confirmedType },
      { headers: this.authHeaders() }).subscribe({
        next: () => { this.successMessage = `Email intake ${status.toLowerCase()}.`; this.loadEmailIntake(); },
        error: error => this.handleApiError(error)
      });
  }

  prepareFnolFromEmail(item: EmailIntakeItem): void {
    if (item.review_status !== 'Reviewed') {
      this.errorMessage = 'Review and confirm the email classification before preparing an FNOL.';
      return;
    }
    const f = item.extracted_fields || {};
    const type = item.confirmed_claim_type || item.suggested_claim_type;
    const commercial = type === 'Commercial Casualty';
    this.draft = {
      customer_name: f['customer_name'] || '',
      email: f['email'] || item.sender_email || '',
      phone: f['phone'] || '',
      policy_number: f['policy_number'] || '',
      claim_type: commercial ? 'Casualty' : 'Property',
      property_occupancy: commercial ? null : null,
      casualty_segment: commercial ? 'Commercial' : null,
      business_name: f['business_name'] || '',
      industry: f['industry'] || '',
      commercial_liability_type: commercial ? (f['commercial_liability_type'] || null) : null,
      incident_date: f['incident_date'] || '',
      incident_description: f['incident_description'] || '',
      loss_location: f['loss_location'] || '',
      country_code: (f['country_code'] || '').toUpperCase(),
      region: '',
      locality: '',
      postal_code: '',
      estimated_loss: f['estimated_loss'] && !isNaN(Number(f['estimated_loss'])) ? Number(f['estimated_loss']) : null,
      currency: (f['currency'] || '').toUpperCase()
    };
    this.selectedEmailIntakeId = item.intake_id;
    this.selectPage('New FNOL');
    this.successMessage = 'Extracted details copied into FNOL. Verify and complete every required field before creating the claim.';
  }

  changeStatus(claim: Claim, status: string): void {
    if (!status || status === claim.status) return;
    this.http.patch<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}/status`, { status }, { headers: this.authHeaders() }).subscribe({
      next: updated => {
        this.claims = this.claims.map(item => item.claim_id === updated.claim_id ? updated : item);
        this.loadDashboard();
      },
      error: error => this.handleApiError(error)
    });
  }

  calculateFinancialEstimate(): void {
    const d = this.estimateDraft;
    const claim = this.claims.find(x => x.claim_id === this.estimateClaimId.trim());
    const currency = (claim?.currency || d.currency || '').toUpperCase();
    const countryCode = (claim?.country_code || d.country_code || '').toUpperCase();
    if (!this.estimateClaimId.trim() || !d.description.trim() || !countryCode ||
        !d.state_or_jurisdiction.trim() || !currency || !/^[A-Z]{3}$/.test(currency) ||
        !/^[A-Z]{2}$/.test(countryCode) || !d.loss_date || !d.tax_point_date ||
        !d.pricing_source.trim() || !d.pricing_effective_date ||
        Number(d.quantity) <= 0 || Number(d.unit_rate) < 0) {
      this.errorMessage = 'Enter a Claim ID, repair details, ISO country and currency codes, loss/tax dates, jurisdiction and verified pricing source.';
      return;
    }
    if (d.tax_mode === 'automatic' && this.applicableTaxRules?.count > 0 && !d.confirm_automatic_tax_application) {
      this.errorMessage = 'Review the matched tax rules and confirm their application before calculating.';
      return;
    }
    if (d.tax_mode === 'manual' && d.taxes.some((tax: any) => Number(tax.rate_percent) > 0 &&
      (!tax.name.trim() || !tax.jurisdiction.trim() || !tax.authority_reference.trim()))) {
      this.errorMessage = 'Each tax component needs a name, jurisdiction and legal/rate authority reference.';
      return;
    }
    if (d.interest_applicable && (!d.interest_authority.trim() || d.interest_rate === null || d.interest_principal === null || d.interest_days === null)) {
      this.errorMessage = 'Applicable interest requires rate, principal, days and a policy/legal authority reference.';
      return;
    }
    const body: any = {
      currency, country_code: countryCode,
      state_or_jurisdiction: d.state_or_jurisdiction, region: claim?.region || d.region, locality: claim?.locality || d.locality,
      postal_code: claim?.postal_code || d.postal_code, loss_date: claim?.incident_date || d.loss_date, tax_point_date: d.tax_point_date,
      line_items: [{ description: d.description, quantity: Number(d.quantity), unit: d.unit, unit_rate: Number(d.unit_rate), taxable: !!d.taxable, depreciation_rate: Number(d.depreciation_rate) }],
      tax_mode: d.tax_mode,
      confirm_automatic_tax_application: !!d.confirm_automatic_tax_application,
      taxes: d.tax_mode === 'manual' ? d.taxes.filter((tax: any) => Number(tax.rate_percent) > 0).map((tax: any) => ({
        ...tax, rate_percent: Number(tax.rate_percent), effective_from: tax.effective_from || null,
        effective_to: tax.effective_to || null
      })) : [],
      charges: d.charges.filter((charge: any) => Number(charge.amount) > 0).map((charge: any) => ({ ...charge, amount: Number(charge.amount) })),
      deductible: Number(d.deductible || 0),
      interest: {
        applicable: !!d.interest_applicable,
        annual_rate_percent: d.interest_applicable ? Number(d.interest_rate) : null,
        principal: d.interest_applicable ? Number(d.interest_principal) : null,
        days: d.interest_applicable ? Number(d.interest_days) : null,
        basis: d.interest_basis, authority_reference: d.interest_authority || ''
      },
      pricing_source: d.pricing_source, pricing_effective_date: d.pricing_effective_date, notes: d.notes
    };
    this.estimateBusy = true; this.errorMessage = ''; this.successMessage = '';
    this.http.post<FinancialEstimate>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(this.estimateClaimId.trim())}/financial-estimates`, body, { headers: this.authHeaders() }).subscribe({
      next: result => { this.financialEstimate = result; this.estimateBusy = false; this.successMessage = 'Preliminary financial estimate calculated and saved for authorised review.'; this.loadFinancialEstimates(); },
      error: error => { this.estimateBusy = false; this.handleApiError(error); }
    });
  }

  loadFinancialEstimates(): void {
    const id = this.estimateClaimId.trim();
    if (!id) { this.errorMessage = 'Enter a Claim ID to load financial estimates.'; return; }
    this.http.get<{items: FinancialEstimate[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/financial-estimates`, { headers: this.authHeaders() }).subscribe({
      next: result => this.financialEstimates = result.items,
      error: error => { this.financialEstimates = []; this.handleApiError(error); }
    });
  }

  canReviewFinancialEstimate(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator', 'Claims Manager', 'Property Adjuster', 'Casualty Adjuster', 'Surveyor', 'Actuarial Analyst', 'Approver'].includes(this.currentUser?.role || '');
  }

  canApproveFinancialEstimate(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator', 'Claims Manager', 'Approver'].includes(this.currentUser?.role || '');
  }

  isFinalEstimateReview(estimate: FinancialEstimate): boolean {
    return ['Approved', 'Rejected'].includes(estimate.review_status || '');
  }

  reviewFinancialEstimate(estimate: FinancialEstimate, status: string): void {
    if (['More Evidence Required', 'Rejected'].includes(status) && !this.financialReviewNotes.trim()) {
      this.errorMessage = 'Add reviewer notes before requesting evidence or rejecting this estimate.';
      return;
    }
    if (['Approved', 'Rejected'].includes(status) && !this.canApproveFinancialEstimate()) {
      this.errorMessage = 'Your role cannot approve or reject financial estimates.';
      return;
    }
    this.financialReviewBusyId = estimate.estimate_id;
    this.http.patch<FinancialEstimate>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(this.estimateClaimId.trim())}/financial-estimates/${encodeURIComponent(estimate.estimate_id)}/review`,
      { status, notes: this.financialReviewNotes.trim() }, { headers: this.authHeaders() }).subscribe({
        next: () => { this.financialReviewBusyId = ''; this.financialReviewNotes = ''; this.successMessage = `Financial estimate review updated: ${status}.`; this.loadFinancialEstimates(); },
        error: error => { this.financialReviewBusyId = ''; this.handleApiError(error); }
      });
  }

  onDamageImageSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] || null;
    this.damageAssessment = null;
    this.errorMessage = '';
    if (this.damageImagePreview) URL.revokeObjectURL(this.damageImagePreview);
    this.damageImagePreview = '';
    this.selectedDamageImage = null;
    if (!file) return;
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    if (!allowed.includes(file.type)) {
      this.errorMessage = 'Choose a JPG, PNG or WEBP image.';
      input.value = '';
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      this.errorMessage = 'Image size must not exceed 10 MB.';
      input.value = '';
      return;
    }
    this.selectedDamageImage = file;
    this.damageImagePreview = URL.createObjectURL(file);
  }

  canReviewDamage(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator', 'Claims Manager', 'Property Adjuster', 'Casualty Adjuster', 'Surveyor'].includes(this.currentUser?.role || '');
  }

  canApproveDamage(): boolean {
    return ['Super Administrator', 'Insurance Company Administrator', 'Claims Manager', 'Approver'].includes(this.currentUser?.role || '');
  }

  loadDamageAssessments(): void {
    const claimId = this.damageClaimId.trim();
    if (!claimId) {
      this.errorMessage = 'Enter a Claim ID to retrieve its saved assessments.';
      return;
    }
    this.http.get<{items: DamageAssessment[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}/damage-assessments`, { headers: this.authHeaders() }).subscribe({
      next: result => {
        this.damageAssessments = result.items;
        this.errorMessage = '';
      },
      error: error => {
        this.damageAssessments = [];
        this.handleApiError(error);
      }
    });
  }

  reviewDamageAssessment(item: DamageAssessment, status: string): void {
    if (['More Evidence Required', 'Rejected'].includes(status) && !this.damageReviewNotes.trim()) {
      this.errorMessage = 'Add reviewer notes before requesting evidence or rejecting the assessment.';
      return;
    }
    this.damageReviewBusyId = item.assessment_id;
    this.errorMessage = '';
    this.http.patch<DamageAssessment>(`${environment.apiBaseUrl}/damage-assessments/${encodeURIComponent(item.assessment_id)}/review`, {
      status, notes: this.damageReviewNotes.trim()
    }, { headers: this.authHeaders() }).subscribe({
      next: updated => {
        this.damageAssessments = this.damageAssessments.map(row => row.assessment_id === updated.assessment_id ? updated : row);
        if (this.damageAssessment?.assessment_id === updated.assessment_id) this.damageAssessment = updated;
        this.damageReviewNotes = '';
        this.damageReviewBusyId = '';
        this.successMessage = `Assessment marked: ${updated.status}.`;
      },
      error: error => {
        this.damageReviewBusyId = '';
        this.handleApiError(error);
      }
    });
  }

  analyseDamage(): void {
    if (!this.selectedDamageImage) {
      this.errorMessage = 'Select a damage image before starting the analysis.';
      return;
    }
    this.errorMessage = '';
    this.successMessage = '';
    this.damageBusy = true;
    const body = new FormData();
    body.append('image', this.selectedDamageImage);
    if (this.damageClaimId.trim()) body.append('claim_id', this.damageClaimId.trim());
    this.http.post<DamageAssessment>(`${environment.apiBaseUrl}/ai/damage-analysis`, body, { headers: this.authHeaders() }).subscribe({
      next: result => {
        this.damageAssessment = result;
        this.damageBusy = false;
        if (result.claim_id) this.loadDamageAssessments();
        this.successMessage = 'Preliminary visual assessment saved. An authorised adjuster must review the findings.';
      },
      error: error => {
        this.damageBusy = false;
        this.handleApiError(error);
      }
    });
  }

  clearDamageAnalysis(): void {
    if (this.damageImagePreview) URL.revokeObjectURL(this.damageImagePreview);
    this.selectedDamageImage = null;
    this.damageImagePreview = '';
    this.damageAssessment = null;
    this.damageAssessments = [];
    this.damageReviewNotes = '';
    this.damageClaimId = '';
  }

  loadUsers(): void {
    if (!this.isAdmin()) return;
    this.http.get<{items: User[]; count: number}>(`${environment.apiBaseUrl}/users`, { headers: this.authHeaders() }).subscribe({
      next: result => this.users = result.items,
      error: error => this.handleApiError(error)
    });
    this.http.get<{roles: string[]}>(`${environment.apiBaseUrl}/users/roles`, { headers: this.authHeaders() }).subscribe({
      next: result => this.roles = result.roles,
      error: error => this.handleApiError(error)
    });
  }

  createUser(): void {
    this.errorMessage = '';
    this.successMessage = '';
    this.userBusy = true;
    this.http.post<User>(`${environment.apiBaseUrl}/users`, this.newUser, { headers: this.authHeaders() }).subscribe({
      next: user => {
        this.userBusy = false;
        this.successMessage = `User ${user.email} created successfully.`;
        this.newUser = { full_name: '', email: '', password: '', role: 'Claims Manager' };
        this.loadUsers();
      },
      error: error => { this.userBusy = false; this.handleApiError(error); }
    });
  }

  private handleApiError(error: any): void {
    if (error.status === 401) {
      this.logout(false);
      this.errorMessage = 'Your session expired. Please sign in again.';
    } else {
      const detail = error.error?.detail;
      this.errorMessage = typeof detail === 'string'
        ? detail
        : (detail?.message || 'The request could not be completed.');
    }
  }

  formatAmount(amount: number | null | undefined, currency: string): string {
    if (amount === null || amount === undefined) return 'Not provided';
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount); }
    catch { return `${currency} ${amount.toLocaleString()}`; }
  }
}
