import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpClientModule, HttpHeaders, HttpParams } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { environment } from '../environments/environment';

interface User { full_name: string; email: string; role: string; roles?: string[]; custom_roles?: any[]; is_active?: boolean; }
interface Adjuster { full_name: string; email: string; role: 'Property Adjuster' | 'Casualty Adjuster'; }
interface Claim {
  claim_id: string; customer_name?: string | null; loss_cause?: string | null; email?: string | null; phone?: string | null;
  policy_number?: string | null; claim_type: 'Property' | 'Casualty' | 'Unclassified'; property_occupancy?: string | null;
  casualty_segment?: 'Commercial' | null; business_name?: string | null; industry?: string | null;
  commercial_liability_type?: string | null; incident_date?: string | null;
  incident_description?: string | null; loss_location?: string | null; country_code?: string | null; region?: string | null; locality?: string | null; postal_code?: string | null; estimated_loss?: number | null;
  currency?: string | null; status: string; created_at?: string; updated_at?: string;
  created_by?: string; created_by_name?: string; created_by_email?: string; source?: string; source_mode?: string;
  assigned_adjuster_email?: string | null; assigned_adjuster_name?: string | null; assigned_adjuster_role?: string | null; closed_at?: string | null;
  auto_intake_status?: string; missing_fields?: string[]; suggested_information?: Record<string, string>;
}
interface ComparisonComment { comment_id: string; claim_id: string; comment: string; field_key?: string | null; field_label?: string | null; created_at: string; created_by: string; created_by_role: string; }
interface AdjusterReport { report_id: string; claim_id: string; report_title: string; report_reference?: string | null; report_date?: string | null; findings?: string | null; report_fields: Record<string, string | number | boolean | null>; created_at: string; created_by: string; }
interface ClaimDocument { file_id: string; claim_id: string; filename: string; content_type: string; size: number; uploaded_at?: string; uploaded_by?: string; }
interface DamageAssessment {
  assessment_id: string; claim_id?: string | null; damage_category: string;
  affected_areas: string[]; visible_observations: string[]; severity: string;
  confidence: string; possible_cause: string; recommended_next_steps: string[];
  limitations: string[]; repair_cost_estimate: number | null; status: string; created_at: string;
  review_history?: { status: string; notes: string; reviewed_at: string; reviewed_by: string; reviewer_role: string }[];
}
interface FinancialEstimate { estimate_id: string; claim_id: string; repair_rate_ids?: string[]; repair_rate_snapshots?: any[]; source_damage_assessment?: { assessment_id: string; status: string; damage_category: string; affected_areas: string[]; visible_observations: string[]; confidence?: string } | null; currency: string; gross_repair_cost: string; replacement_cost_value: string; depreciation: string; net_repair_cost: string; actual_cash_value: string; valuation_basis_note?: string; total_fees_and_charges: string; total_tax: string; interest: { amount: string }; deductible: string; total_before_deductible: string; preliminary_net_estimate: string; status: string; created_at: string; pricing_source: string; state_or_jurisdiction: string; taxes: any[]; line_items: any[]; tax_mode?: string; tax_profile_status?: string; tax_rule_warning?: string | null; tax_rule_ids?: string[]; review_status?: string; review_history?: any[]; }
interface EmailConnectorStatus { gmail: { configured: boolean; watch_active: boolean; watch_expiration?: string | null }; microsoft365: { configured: boolean; subscription_active: boolean; subscription_expiration?: string | null }; note: string; }
interface EmailIntakeItem { intake_id: string; sender_email: string; subject: string; received_at: string; suggested_claim_type: string; confirmed_claim_type?: string; classification_reason: string; missing_fields: string[]; review_status: string; extracted_fields: Record<string, string | null>; claim_id?: string; }
interface ClaimDraft {
  customer_name: string; loss_cause: string; email: string; phone: string; policy_number: string;
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
  effectivePermissions: string[] = [];
  loginEmail = '';
  loginPassword = '';
  loginBusy = false;
  activePage = 'Dashboard';
  isSidebarCollapsed = false;

  toggleSidebar(): void {
    this.isSidebarCollapsed = !this.isSidebarCollapsed;
  }
  settingsSection = 'Company Profile';
  selectedAIProvider = '';
  readonly aiProviderSetup: Record<string, {purpose: string; environmentKey: string; setup: string; note: string}> = {
    'ChatGPT': { purpose: 'Claim summarisation, structured reasoning and document assistance.', environmentKey: 'OPENAI_API_KEY', setup: 'Add OPENAI_API_KEY to the solv-in backend service environment in Render, then redeploy the backend.', note: 'Use a restricted project key and configure usage limits at the provider.' },
    'Claude': { purpose: 'Long-document analysis, policy wording review and report drafting.', environmentKey: 'ANTHROPIC_API_KEY', setup: 'Add ANTHROPIC_API_KEY to the solv-in backend service environment in Render, then redeploy the backend.', note: 'Use a dedicated key with appropriate spending limits.' },
    'Gemini': { purpose: 'Existing image damage analysis and document extraction.', environmentKey: 'GEMINI_API_KEY', setup: 'Gemini is already used by the backend. Check the existing GEMINI_API_KEY in Render if the feature is unavailable.', note: 'Do not paste or expose the key in the browser.' },
    'GitHub Copilot': { purpose: 'Developer productivity, code assistance and software testing.', environmentKey: '', setup: 'Copilot is managed through GitHub user or organisation licensing and IDE sign-in. It is not a claims-processing model endpoint.', note: 'Manage seats and access in GitHub; no Solv-In API key is required.' }
  };
  apiStatus = 'Checking API…';
  loadingClaims = false;
  savingClaim = false;
  errorMessage = '';
  successMessage = '';
  searchTerm = '';
  lossCauseFilter = '';
  lossCauseOptions: string[] = [];
  statusFilter = '';
  dashboardClaimsQuickFilter: 'all' | 'open' | 'pending' | 'closed' | 'overdue' = 'all';
  claims: Claim[] = [];
  selectedClaim: Claim | null = null;
  claimDetailSearch = '';
  claimComparisonTab: 'initial' | 'adjuster' | 'estimates' = 'initial';
  expandedClaimSections: string[] = ['initial-review'];
  selectedComparisonSectionId = 'initial-review';
  selectedComparisonSubsectionId = '';
  comparisonComments: ComparisonComment[] = [];
  comparisonCommentsLoading = false;
  comparisonCommentBusy = false;
  comparisonCommentDraft = '';
  comparisonCommentFieldKey = '';
  expandedClaimSubsections: string[] = ['initial-review:claim-status'];
  loadingClaimDetails = false;
  claimEditMode = false;
  claimEditBusy = false;
  claimDeleteBusy = false;
  exportingClaimId = '';
  editingClaimField: string | null = null;
  claimFieldDraft = '';
  claimFieldBusy = false;
  claimEditDraft: ClaimDraft = this.emptyDraft();
  claimDocuments: ClaimDocument[] = [];
  selectedClaimDocument: File | null = null;
  selectedFnolDocument: File | null = null;
  documentUploadBusy = false;
  documentsLoading = false;
  users: User[] = [];
  roles: string[] = [];
  customRoles: any[] = [];
  availablePermissions: string[] = [];
  customRoleDraft = { name: '', description: '', permissions: [] as string[] };
  customRoleBusy = false;
  editingCustomRoleName = '';
  selectedUserForRoles: User | null = null;
  roleAssignmentDraft = { roles: [] as string[], custom_roles: [] as string[] };
  roleAssignmentBusy = false;
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
  adjusterReports: AdjusterReport[] = [];
  adjusterReportsBusy = false;
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
  usTaxResearchMatrix: any = null;
  usTaxResearchBusy = false;

  taxRuleDraft: any = this.emptyTaxRuleDraft();
  taxRulesBusy = false;
  taxRuleCountryFilter = '';
  repairRates: any[] = [];
  repairRateDraft: any = this.emptyRepairRateDraft();
  repairRateBusy = false;
  repairRateCountryFilter = '';
  repairRateStateFilter = '';
  repairRateCityFilter = '';
  repairRateStatusFilter = '';
  applicableTaxRules: any = null;
  applicableRepairRates: any[] = [];
  selectedRepairRateId = '';
  repairRatesLookupBusy = false;
  estimateDraft: any = this.emptyEstimateDraft();
  newUser = { full_name: '', email: '', password: '', roles: ['Claims Manager'] as string[] };
  totalClaims = 0;
  newClaims = 0;
  investigatingClaims = 0;
  pendingClaims = 0;
  closedClaims = 0;
  unclassifiedLegacyCasualtyClaims = 0;
  adjusters: Adjuster[] = [];
  dashboardAnalytics: any = null;
  dashboardBusy = false;
  dashboardYear = '';
  dashboardMonth = '';
  dashboardStatus = '';
  dashboardClaimType = 'All';
  dashboardAdjuster = '';
  readonly dashboardMonths = [{value:'1',label:'January'},{value:'2',label:'February'},{value:'3',label:'March'},{value:'4',label:'April'},{value:'5',label:'May'},{value:'6',label:'June'},{value:'7',label:'July'},{value:'8',label:'August'},{value:'9',label:'September'},{value:'10',label:'October'},{value:'11',label:'November'},{value:'12',label:'December'}];
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
  readonly navigation = ['Dashboard', 'Claims', 'AI Damage Analysis', 'Policy Coverage', 'Financial Estimate', 'Settings'];
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
    this.loadEffectivePermissions(() => {
      if (this.hasPermission('dashboard.view')) this.loadDashboard();
      if (this.hasPermission('analytics.view')) this.loadDashboardAnalytics();
      if (this.hasPermission('claims.assign')) this.loadAdjusters();
      if (this.hasPermission('claims.view')) this.loadClaims();
      if (this.isAdmin()) this.loadUsers();
    });
  }

  loadEffectivePermissions(onLoaded?: () => void): void {
    this.http.get<{roles: string[]; permissions: string[]}>(`${environment.apiBaseUrl}/auth/permissions`, { headers: this.authHeaders() }).subscribe({
      next: result => {
        this.effectivePermissions = result.permissions || [];
        onLoaded?.();
      },
      error: error => {
        this.effectivePermissions = [];
        this.handleApiError(error);
      }
    });
  }

  hasPermission(permission: string): boolean {
    return this.effectivePermissions.includes(permission);
  }

  canAccessPage(page: string): boolean {
    const required: Record<string, string[]> = {
      'Dashboard': ['dashboard.view'],
      'Claims': ['claims.view'],
      'AI Damage Analysis': ['damage.analyse'],
      'Policy Coverage': ['coverage.view'],
      'Financial Estimate': ['finance.view', 'finance.edit', 'finance.approve'],
    };
    if (page === 'Settings') return this.isAdmin();
    const permissions = required[page] || [];
    return permissions.some(permission => this.hasPermission(permission));
  }

  logout(showMessage = true): void {
    sessionStorage.removeItem('solvin_access_token');
    this.currentUser = null;
    this.effectivePermissions = [];
    this.isAuthenticated = false;
    this.activePage = 'Dashboard';
    this.claims = [];
    this.users = [];
    this.errorMessage = '';
    this.successMessage = showMessage ? 'You have signed out.' : '';
    this.checkApi();
  }

  isAdmin(): boolean {
    return this.currentUser?.roles?.includes('Super Administrator') || this.currentUser?.role === 'Super Administrator';
  }

  canManageTaxRules(): boolean { return this.hasPermission('roles.manage'); }

  canManagePolicies(): boolean { return this.hasPermission('roles.manage'); }

  canViewPolicyCoverage(): boolean { return this.hasPermission('coverage.view'); }

  latestFinancialReview(estimate: FinancialEstimate): any | null {
    const history = estimate.review_history;
    return history?.length ? history[history.length - 1] : null;
  }

  canAnalyseDamage(): boolean { return this.hasPermission('damage.analyse'); }

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
    const id = this.selectedClaim?.claim_id || this.coverageClaimId.trim();
    if (!id) { this.coverageAssessments = []; return; }
    this.coverageClaimId = id;
    this.http.get<{items: any[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/coverage-assessments`, { headers: this.authHeaders() }).subscribe({
      next: result => this.coverageAssessments = result.items,
      error: error => this.handleApiError(error)
    });
  }

  emptyTaxRuleDraft(): any {
    return { country_code: '', currency: '', admin_area: '', region: '', locality: '', tax_name: '', tax_type: 'Sales tax', rate_percent: 0, calculation_order: 100, applies_to: 'depreciated_repair_subtotal', compound_on_prior_taxes: false, tax_point_type: 'Tax point date', effective_from: new Date().toISOString().slice(0, 10), effective_to: '', authority_name: '', authority_reference: '', source_url: '', notes: '' };
  }

  loadUsTaxResearchMatrix(): void {
    this.usTaxResearchBusy = true;
    this.http.get<any>(`${environment.apiBaseUrl}/tax-research/us-water-restoration`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.usTaxResearchMatrix = result; this.usTaxResearchBusy = false; },
      error: error => { this.usTaxResearchBusy = false; this.handleApiError(error); }
    });
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

  emptyRepairRateDraft(): any {
    return {
      country_code: 'IN', currency: 'INR', state: '', city: '', category: 'Structural',
      item_code: '', item_name: '', description: '', unit: 'sq.ft', unit_rate: null,
      effective_from: new Date().toISOString().slice(0, 10), effective_to: '',
      source_name: '', source_reference: '', source_url: '', notes: ''
    };
  }

  loadRepairRates(): void {
    let params = new HttpParams();
    if (this.repairRateCountryFilter.trim()) params = params.set('country_code', this.repairRateCountryFilter.trim().toUpperCase());
    if (this.repairRateStateFilter.trim()) params = params.set('state', this.repairRateStateFilter.trim());
    if (this.repairRateCityFilter.trim()) params = params.set('city', this.repairRateCityFilter.trim());
    if (this.repairRateStatusFilter) params = params.set('status', this.repairRateStatusFilter);
    this.http.get<{items: any[]; count: number}>(`${environment.apiBaseUrl}/repair-rates`, {
      params, headers: this.authHeaders()
    }).subscribe({
      next: result => this.repairRates = result.items,
      error: error => this.handleApiError(error)
    });
  }

  saveRepairRate(): void {
    const d = this.repairRateDraft;
    if (!/^[A-Z]{2}$/i.test(d.country_code) || !/^[A-Z]{3}$/i.test(d.currency) ||
        !d.state.trim() || !d.city.trim() || !d.category.trim() || !d.item_code.trim() ||
        !d.item_name.trim() || !d.unit.trim() || Number(d.unit_rate) <= 0 ||
        !d.effective_from || !d.source_name.trim() || !d.source_reference.trim()) {
      this.errorMessage = 'Complete country, currency, state, city, repair item, positive unit rate, effective date and pricing source.';
      return;
    }
    this.repairRateBusy = true;
    this.errorMessage = '';
    const body = {
      ...d, country_code: d.country_code.toUpperCase(), currency: d.currency.toUpperCase(),
      unit_rate: Number(d.unit_rate), effective_to: d.effective_to || null,
      source_url: d.source_url || null
    };
    this.http.post<any>(`${environment.apiBaseUrl}/repair-rates`, body, { headers: this.authHeaders() }).subscribe({
      next: () => {
        this.repairRateBusy = false;
        this.successMessage = 'Repair rate saved as Draft. An authorised administrator must verify its source before activation.';
        this.repairRateDraft = this.emptyRepairRateDraft();
        this.loadRepairRates();
      },
      error: error => { this.repairRateBusy = false; this.handleApiError(error); }
    });
  }

  setRepairRateStatus(rate: any, status: 'Active' | 'Inactive'): void {
    const note = status === 'Active'
      ? (window.prompt('Enter how this pricing source and rate were verified:') || '')
      : 'Deactivated by authorised administrator.';
    if (status === 'Active' && !note.trim()) return;
    this.http.patch<any>(`${environment.apiBaseUrl}/repair-rates/${encodeURIComponent(rate.rate_id)}/status`,
      { status, verification_note: note }, { headers: this.authHeaders() }).subscribe({
        next: () => { this.successMessage = `Repair rate ${status.toLowerCase()}.`; this.loadRepairRates(); },
        error: error => this.handleApiError(error)
      });
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
      line_items: [this.emptyEstimateLineItem()], tax_mode: 'automatic',
      source_damage_assessment_id: null,
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
      customer_name: '', loss_cause: '', email: '', phone: '', policy_number: '',
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
    this.activePage = page;
    if (page === 'Settings') {
      this.settingsSection = 'Company Profile';
      if (this.isAdmin()) this.loadUsers();
      if (this.canManageEmailConnectors()) this.loadEmailConnectorStatus();
    }
    this.errorMessage = '';
    this.successMessage = '';
    if (page === 'Claims') this.loadClaims();
    if (page === 'AI Damage Analysis' && this.damageClaimId.trim()) this.loadDamageAssessments();
    if (page === 'Financial Estimate' && this.canManageTaxRules()) { this.loadTaxRules(); this.loadUsTaxResearchMatrix(); }
    if (page === 'Repair Rates' && this.canManageTaxRules()) this.loadRepairRates();
    if (page === 'Policy Coverage') this.loadPolicies();
    if (page === 'Dashboard') this.loadDashboardAnalytics();
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


  loadAdjusters(): void {
    this.http.get<{items: Adjuster[]; count: number}>(`${environment.apiBaseUrl}/adjusters`, { headers: this.authHeaders() }).subscribe({ next: result => this.adjusters = result.items || [], error: error => this.handleApiError(error) });
  }

  canAssignClaims(): boolean { return this.hasPermission('claims.assign'); }

  loadDashboardAnalytics(): void {
    this.dashboardBusy = true;
    let params = new HttpParams();
    if (this.dashboardYear) params = params.set('year', this.dashboardYear);
    if (this.dashboardMonth) params = params.set('month', this.dashboardMonth);
    if (this.dashboardStatus) params = params.set('status', this.dashboardStatus);
    if (this.dashboardClaimType !== 'All') params = params.set('claim_type', this.dashboardClaimType);
    if (this.dashboardAdjuster) params = params.set('adjuster_email', this.dashboardAdjuster);
    this.http.get<any>(`${environment.apiBaseUrl}/dashboard/analytics`, { params, headers: this.authHeaders() }).subscribe({ next: data => { this.dashboardAnalytics = data; this.dashboardBusy = false; }, error: error => { this.dashboardBusy = false; this.handleApiError(error); } });
  }

  assignAdjuster(claim: Claim, email: string): void {
    if (!this.canAssignClaims() || !email) return;
    this.http.patch<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}/assign-adjuster`, { adjuster_email: email }, { headers: this.authHeaders() }).subscribe({ next: updated => { this.claims = this.claims.map(item => item.claim_id === updated.claim_id ? updated : item); this.successMessage = `Assigned ${updated.claim_id} to ${updated.assigned_adjuster_name}.`; this.loadDashboardAnalytics(); }, error: error => this.handleApiError(error) });
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

  loadLossCauseOptions(): void {
    this.http.get<{items: string[]; count: number}>(`${environment.apiBaseUrl}/claims/loss-causes`, {
      headers: this.authHeaders()
    }).subscribe({
      next: result => {
        this.lossCauseOptions = result.items || [];
        if (this.lossCauseFilter && !this.lossCauseOptions.includes(this.lossCauseFilter)) {
          this.lossCauseFilter = '';
        }
      },
      error: error => this.handleApiError(error)
    });
  }

  loadClaims(): void {
    this.loadingClaims = true;
    let params = new HttpParams();
    if (this.searchTerm.trim()) params = params.set('search', this.searchTerm.trim());
    if (this.lossCauseFilter) params = params.set('loss_cause', this.lossCauseFilter);
    if (this.statusFilter) params = params.set('status', this.statusFilter);
    this.http.get<{items: Claim[]; count: number}>(`${environment.apiBaseUrl}/claims`, { params, headers: this.authHeaders() }).subscribe({
      next: result => { this.claims = result.items; this.loadingClaims = false; this.loadLossCauseOptions(); },
      error: error => { this.loadingClaims = false; this.handleApiError(error); this.claims = []; }
    });
  }

  openDashboardClaimsFilter(filter: 'all' | 'open' | 'pending' | 'closed' | 'overdue'): void {
    this.dashboardClaimsQuickFilter = filter;
    this.searchTerm = '';
    this.lossCauseFilter = '';
    this.statusFilter = '';
    this.activePage = 'Claims';
    this.errorMessage = '';
    this.successMessage = '';
    this.loadClaims();
  }

  filteredClaims(): Claim[] {
    switch (this.dashboardClaimsQuickFilter) {
      case 'open': return this.claims.filter(claim => claim.status !== 'Closed');
      case 'pending': return this.claims.filter(claim => claim.status === 'Pending Approval');
      case 'closed': return this.claims.filter(claim => claim.status === 'Closed');
      case 'overdue': {
        const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
        return this.claims.filter(claim => claim.status !== 'Closed' && !!claim.created_at && new Date(claim.created_at).getTime() < cutoff);
      }
      default: return this.claims;
    }
  }

  createdByTooltip(claim: Claim): string {
    const name = claim.created_by_name || claim.created_by || 'Not recorded';
    const email = claim.created_by_email || 'Not recorded';
    return `Created by: ${name}\nEmail address: ${email}`;
  }

  openClaimDetails(claim: Claim, editAfterOpen = false): void {
    this.errorMessage = '';
    this.successMessage = '';
    this.loadingClaimDetails = true;
    this.selectedClaim = null;
    this.claimDetailSearch = '';
    this.claimComparisonTab = 'initial';
    this.estimateClaimId = claim.claim_id;
    this.financialEstimates = [];
    this.adjusterReports = [];
    this.comparisonComments = [];
    this.comparisonCommentDraft = '';
    this.comparisonCommentFieldKey = '';
    this.selectedComparisonSectionId = 'initial-review';
    this.expandedClaimSections = ['initial-review'];
    this.expandedClaimSubsections = ['initial-review:claim-status'];
    this.activePage = 'Claim Details';
    this.http.get<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.selectedClaim = result; this.loadingClaimDetails = false; this.loadClaimDocuments(); this.loadFinancialEstimates(); this.loadAdjusterReports(); this.loadCoverageAssessments(); this.loadComparisonComments(); if (editAfterOpen) this.startClaimEdit(); },
      error: error => { this.loadingClaimDetails = false; this.handleApiError(error); }
    });
  }

  canEditClaims(): boolean { return this.hasPermission('claims.edit'); }

  canDeleteClaims(): boolean { return this.hasPermission('claims.delete'); }

  startClaimEdit(): void {
    if (!this.selectedClaim || !this.canEditClaims()) return;
    const claim = this.selectedClaim;
    this.claimEditDraft = {
      customer_name: claim.customer_name || '',
      loss_cause: claim.loss_cause || '',
      email: claim.email || '',
      phone: claim.phone || '',
      policy_number: claim.policy_number || '',
      claim_type: claim.claim_type === 'Casualty' ? 'Casualty' : 'Property',
      property_occupancy: claim.property_occupancy || (claim.claim_type === 'Property' ? 'Residential' : null),
      casualty_segment: claim.claim_type === 'Casualty' ? 'Commercial' : null,
      business_name: claim.business_name || '',
      industry: claim.industry || '',
      commercial_liability_type: claim.commercial_liability_type || (claim.claim_type === 'Casualty' ? 'Commercial General Liability' : null),
      incident_date: (claim.incident_date || '').slice(0, 10),
      incident_description: claim.incident_description || '',
      loss_location: claim.loss_location || '',
      country_code: (claim.country_code || '').toUpperCase(),
      region: claim.region || '',
      locality: claim.locality || '',
      postal_code: claim.postal_code || '',
      estimated_loss: claim.estimated_loss ?? null,
      currency: (claim.currency || '').toUpperCase()
    };
    this.claimEditMode = true;
    this.errorMessage = '';
    this.successMessage = '';
  }

  cancelClaimEdit(): void {
    this.claimEditMode = false;
    this.claimEditDraft = this.emptyDraft();
  }

  onClaimEditTypeChange(): void {
    if (this.claimEditDraft.claim_type === 'Casualty') {
      this.claimEditDraft.property_occupancy = null;
      this.claimEditDraft.casualty_segment = 'Commercial';
      this.claimEditDraft.commercial_liability_type = this.claimEditDraft.commercial_liability_type || 'Commercial General Liability';
    } else {
      this.claimEditDraft.property_occupancy = this.claimEditDraft.property_occupancy || 'Residential';
      this.claimEditDraft.casualty_segment = null;
      this.claimEditDraft.business_name = '';
      this.claimEditDraft.industry = '';
      this.claimEditDraft.commercial_liability_type = null;
    }
  }

  saveClaimEdit(): void {
    if (!this.selectedClaim || !this.canEditClaims()) return;
    const d = this.claimEditDraft;
    if (!d.customer_name.trim() || !d.policy_number.trim() || !d.incident_date ||
        !d.incident_description.trim() || !d.loss_location.trim() ||
        !/^[A-Z]{2}$/i.test(d.country_code.trim()) || !/^[A-Z]{3}$/i.test(d.currency.trim()) ||
        (d.claim_type === 'Casualty' && (!d.business_name.trim() || !d.industry.trim() || !d.commercial_liability_type))) {
      this.errorMessage = 'Complete all required fields. Country must be a 2-letter code and currency a 3-letter ISO code.';
      return;
    }
    this.claimEditBusy = true;
    this.errorMessage = '';
    const payload = {
      ...d,
      country_code: d.country_code.trim().toUpperCase(),
      currency: d.currency.trim().toUpperCase(),
      estimated_loss: d.estimated_loss === null || d.estimated_loss === ('' as any) ? null : Number(d.estimated_loss),
      casualty_segment: d.claim_type === 'Casualty' ? 'Commercial' : null,
      property_occupancy: d.claim_type === 'Property' ? d.property_occupancy : null,
      business_name: d.claim_type === 'Casualty' ? d.business_name : null,
      industry: d.claim_type === 'Casualty' ? d.industry : null,
      commercial_liability_type: d.claim_type === 'Casualty' ? d.commercial_liability_type : null,
      status: this.selectedClaim.status
    };
    this.http.patch<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(this.selectedClaim.claim_id)}`, payload, { headers: this.authHeaders() }).subscribe({
      next: updated => {
        this.selectedClaim = updated;
        this.claims = this.claims.map(item => item.claim_id === updated.claim_id ? updated : item);
        this.claimEditBusy = false;
        this.claimEditMode = false;
        this.successMessage = `Claim ${updated.claim_id} updated successfully.`;
        this.loadDashboard();
        this.loadLossCauseOptions();
      },
      error: error => { this.claimEditBusy = false; this.handleApiError(error); }
    });
  }

  deleteClaim(): void {
    if (!this.selectedClaim || !this.canDeleteClaims() || this.claimDeleteBusy) return;
    const claimId = this.selectedClaim.claim_id;
    const confirmed = window.confirm(`Permanently delete claim ${claimId}, its uploaded evidence, and linked assessments/estimates? This action cannot be undone.`);
    if (!confirmed) return;
    this.claimDeleteBusy = true;
    this.errorMessage = '';
    this.http.delete<{deleted: boolean; claim_id: string}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}`, { headers: this.authHeaders() }).subscribe({
      next: () => {
        this.claimDeleteBusy = false;
        this.selectedClaim = null;
        this.claimDocuments = [];
        this.claimEditMode = false;
        this.activePage = 'Claims';
        this.successMessage = `Claim ${claimId} and its linked records were deleted.`;
        this.loadClaims();
        this.loadDashboard();
      },
      error: error => { this.claimDeleteBusy = false; this.handleApiError(error); }
    });
  }

  openClaimForEdit(claim: Claim): void {
    if (!this.canEditClaims()) return;
    this.openClaimDetails(claim, true);
  }

  deleteClaimFromRegister(claim: Claim): void {
    if (!this.canDeleteClaims()) return;
    this.selectedClaim = claim;
    this.deleteClaim();
  }


  claimDetailFields(): Array<{key: string; label: string; value: string; type: string; required: boolean; options?: string[]}> {
    const c = this.selectedClaim;
    if (!c) return [];
    const fields: Array<{key: string; label: string; value: string; type: string; required: boolean; options?: string[]}> = [
      {key:'customer_name',label:'Insured name',value:c.customer_name || '',type:'text',required:true},
      {key:'loss_cause',label:'Loss cause',value:c.loss_cause || '',type:'text',required:false},
      {key:'policy_number',label:'Policy number',value:c.policy_number || '',type:'text',required:true},
      {key:'email',label:'Email',value:c.email || '',type:'email',required:false},
      {key:'phone',label:'Phone',value:c.phone || '',type:'tel',required:false},
      {key:'claim_type',label:'Claim type',value:c.claim_type || '',type:'select',required:true,options:['Property','Casualty']},
      {key:'incident_date',label:'Incident date',value:(c.incident_date || '').slice(0,10),type:'date',required:true},
      {key:'estimated_loss',label:'Estimated loss',value:c.estimated_loss === null || c.estimated_loss === undefined ? '' : String(c.estimated_loss),type:'number',required:false},
      {key:'currency',label:'Currency',value:c.currency || '',type:'text',required:true},
      {key:'loss_location',label:'Loss location',value:c.loss_location || '',type:'text',required:true},
      {key:'country_code',label:'Country code',value:c.country_code || '',type:'text',required:true},
      {key:'region',label:'State / region',value:c.region || '',type:'text',required:false},
      {key:'locality',label:'City / locality',value:c.locality || '',type:'text',required:false},
      {key:'postal_code',label:'Postal code',value:c.postal_code || '',type:'text',required:false},
      {key:'incident_description',label:'Incident description',value:c.incident_description || '',type:'textarea',required:true}
    ];
    if (c.claim_type === 'Property') {
      fields.splice(6, 0, {key:'property_occupancy',label:'Property occupancy',value:c.property_occupancy || '',type:'select',required:false,options:['Residential','Commercial','Industrial','Mixed Use','Other']});
    } else if (c.claim_type === 'Casualty') {
      fields.splice(6, 0,
        {key:'business_name',label:'Business / insured entity',value:c.business_name || '',type:'text',required:true},
        {key:'industry',label:'Industry',value:c.industry || '',type:'text',required:true},
        {key:'commercial_liability_type',label:'Commercial liability type',value:c.commercial_liability_type || '',type:'select',required:true,options:['Commercial General Liability','Professional Liability','Product Liability','Employers Liability','Public Liability','Other Commercial Liability']}
      );
    }
    fields.splice(1, 0, {key:'status',label:'Claim status',value:c.status || '',type:'select',required:true,options:[...this.statuses]});
    return fields;
  }

  claimReportComparisonRows(): Array<{key:string;label:string;initial:string;adjuster:string;estimate:string;policy:string;status:'Match'|'Mismatch'|'Not Available'}> {
    const claim: any = this.selectedClaim || {};
    const adjusterReport: any = this.adjusterReports.length ? this.adjusterReports[0].report_fields : null;
    const estimate: any = this.financialEstimates.length ? this.financialEstimates[0] : null;
    const policySnapshot = this.coverageAssessments[0]?.policy_snapshot || {};
    return this.claimDetailFields().map(field => {
      const initial = String((claim as any)[field.key] ?? '').trim();
      const adjuster = String(adjusterReport?.[field.key] ?? '').trim();
      let estimateRaw: any = estimate?.[field.key];
      if (field.key === 'estimated_loss' && estimate) estimateRaw = estimate.preliminary_net_estimate;
      if (field.key === 'status' && estimate) estimateRaw = estimate.review_status || estimate.status;
      const estimateValue = String(estimateRaw ?? '').trim();
      const policyRaw = field.key === 'policy_number' ? (policySnapshot.policy_number || claim.policy_number)
        : field.key === 'currency' ? policySnapshot.currency
        : field.key === 'estimated_loss' ? undefined : undefined;
      const policyValue = String(policyRaw ?? '').trim();
      const values = [initial, adjuster, estimateValue, policyValue].filter(value => value.length > 0);
      const status: 'Match'|'Mismatch'|'Not Available' = values.length < 2
        ? 'Not Available'
        : values.every(value => value.toLocaleLowerCase() === values[0].toLocaleLowerCase()) ? 'Match' : 'Mismatch';
      const display = (value: string, currency?: string) => {
        if (!value) return 'Not Available';
        if (field.key === 'estimated_loss' && !Number.isNaN(Number(value))) return this.formatAmount(Number(value), currency || claim.currency || estimate?.currency);
        return value;
      };
      return {
        key: field.key,
        label: field.label,
        initial: display(initial, claim.currency),
        adjuster: display(adjuster, claim.currency),
        estimate: display(estimateValue, estimate?.currency),
        policy: display(policyValue, policySnapshot.currency),
        status
      };
    });
  }

  comparisonRowsForSection(section: {subsections: Array<{id?: string; title?: string; fieldKeys: string[]}>}, selectedSubsectionId = ''): ReturnType<AppComponent['claimReportComparisonRows']> {
    const activeSubsections = selectedSubsectionId ? section.subsections.filter(subsection => subsection.id === selectedSubsectionId) : section.subsections;
    const baseRows = this.claimReportComparisonRows();
    const keys = new Set(activeSubsections.flatMap(subsection => subsection.fieldKeys));
    const rows = baseRows.filter(row => keys.has(row.key));
    const claim: any = this.selectedClaim || {};
    const adjuster: any = this.adjusterReports[0]?.report_fields || {};
    const estimate: any = this.financialEstimates[0] || {};
    const assessment: any = this.coverageAssessments[0] || {};
    const snapshot: any = assessment.policy_snapshot || {};
    const definitions: Record<string, Array<{key:string;label:string;aliases:string[]}>> = {
      'carrier-1': [{key:'carrier_1_name',label:'Carrier 1 name',aliases:['carrier_1_name','carrier_name_1','lead_carrier_name']},{key:'carrier_1_share',label:'Carrier 1 participation share',aliases:['carrier_1_share','carrier_1_percentage','carrier_share_1']}],
      'carrier-2': [{key:'carrier_2_name',label:'Carrier 2 name',aliases:['carrier_2_name','carrier_name_2']},{key:'carrier_2_share',label:'Carrier 2 participation share',aliases:['carrier_2_share','carrier_2_percentage','carrier_share_2']}],
      'carrier-3': [{key:'carrier_3_name',label:'Carrier 3 name',aliases:['carrier_3_name','carrier_name_3']},{key:'carrier_3_share',label:'Carrier 3 participation share',aliases:['carrier_3_share','carrier_3_percentage','carrier_share_3']}],
      'carrier-4': [{key:'carrier_4_name',label:'Carrier 4 name',aliases:['carrier_4_name','carrier_name_4']},{key:'carrier_4_share',label:'Carrier 4 participation share',aliases:['carrier_4_share','carrier_4_percentage','carrier_share_4']}],
      'carrier-5': [{key:'carrier_5_name',label:'Carrier 5 name',aliases:['carrier_5_name','carrier_name_5']},{key:'carrier_5_share',label:'Carrier 5 participation share',aliases:['carrier_5_share','carrier_5_percentage','carrier_share_5']}],
      'carrier-6': [{key:'carrier_6_name',label:'Carrier 6 name',aliases:['carrier_6_name','carrier_name_6']},{key:'carrier_6_share',label:'Carrier 6 participation share',aliases:['carrier_6_share','carrier_6_percentage','carrier_share_6']}],
      'policy-period': [{key:'effective_from',label:'Policy effective from',aliases:['effective_from','policy_effective_from']},{key:'effective_to',label:'Policy effective to',aliases:['effective_to','policy_effective_to']}],
      'recovery-assessment': [{key:'recovery_status',label:'Recovery status',aliases:['recovery_status','recovery_assessment_status']},{key:'recovery_potential',label:'Recovery potential',aliases:['recovery_potential','recoverable_amount']},{key:'recovery_notes',label:'Recovery notes',aliases:['recovery_notes','recovery_assessment_notes']}],
      'subrogation': [{key:'subrogation_status',label:'Subrogation status',aliases:['subrogation_status']},{key:'subrogation_potential',label:'Subrogation potential',aliases:['subrogation_potential','subrogation_amount']},{key:'responsible_party',label:'Potential responsible party',aliases:['responsible_party','at_fault_party','third_party_name']}],
      'salvage': [{key:'salvage_status',label:'Salvage status',aliases:['salvage_status']},{key:'salvage_value',label:'Estimated salvage value',aliases:['salvage_value','salvage_amount']},{key:'salvage_notes',label:'Salvage notes',aliases:['salvage_notes']}],
      'deductibles': [{key:'deductible',label:'Policy deductible',aliases:['deductible','policy_deductible']},{key:'deductible_type',label:'Deductible type',aliases:['deductible_type']},{key:'deductible_basis',label:'Deductible basis',aliases:['deductible_basis']}],
      'mortgage-details': [{key:'mortgagee_name',label:'Mortgagee name',aliases:['mortgagee_name','mortgage_holder']},{key:'mortgagee_clause',label:'Mortgagee clause',aliases:['mortgagee_clause']},{key:'loan_number',label:'Loan / reference number',aliases:['loan_number','mortgage_loan_number']}],
      'coverage-review': [{key:'coverage_status',label:'Coverage assessment status',aliases:['coverage_status','status','decision']},{key:'coverage_decision',label:'Coverage decision',aliases:['coverage_decision','decision','recommendation']},{key:'loss_category',label:'Assessed loss category',aliases:['loss_category']},{key:'covered_loss_categories',label:'Covered loss categories',aliases:['covered_loss_categories']},{key:'excluded_loss_categories',label:'Excluded loss categories',aliases:['excluded_loss_categories']},{key:'per_occurrence_limit',label:'Per-occurrence coverage limit',aliases:['per_occurrence_limit']},{key:'aggregate_limit',label:'Aggregate coverage limit',aliases:['aggregate_limit']},{key:'deductible',label:'Applicable deductible',aliases:['deductible']}],
      'building-information': [{key:'building_year_built',label:'Building year built',aliases:['building_year_built','year_built']},{key:'construction_type',label:'Construction type',aliases:['construction_type','building_construction']},{key:'building_area',label:'Building area',aliases:['building_area','square_footage','area_sq_ft']}],
      'roof-information': [{key:'roof_type',label:'Roof type',aliases:['roof_type']},{key:'roof_age',label:'Roof age',aliases:['roof_age']},{key:'roof_condition',label:'Roof condition',aliases:['roof_condition']}],
      'property-coverage': [{key:'property_coverage_type',label:'Property coverage type',aliases:['property_coverage_type','coverage_type']},{key:'property_coverage_limit',label:'Property coverage limit',aliases:['property_coverage_limit','coverage_limit','per_occurrence_limit']},{key:'insured_value',label:'Insured value',aliases:['insured_value','building_insured_value']}],
      'endorsements': [{key:'endorsements',label:'Policy endorsements',aliases:['endorsements']},{key:'policy_wording_reference',label:'Policy wording reference',aliases:['policy_wording_reference']}]
    };
    const read = (source: any, aliases: string[]): string => {
      for (const alias of aliases) {
        const value = source?.[alias];
        if (value !== undefined && value !== null && value !== '') return Array.isArray(value) ? value.join(', ') : String(value);
      }
      return '';
    };
    for (const subsection of activeSubsections) {
      if (subsection.fieldKeys.length) continue;
      const defs = definitions[subsection.id || ''] || (subsection.id ? [{key:subsection.id,label:subsection.title || subsection.id,aliases:[subsection.id]}] : []);
      for (const def of defs) {
        const initial = read(claim, def.aliases) || (['coverage-review','policy-period','deductibles','endorsements','property-coverage'].includes(subsection.id || '') ? read(assessment, def.aliases) : '');
        const adjusterValue = read(adjuster, def.aliases);
        const estimateValue = read(estimate, def.aliases);
        const policyValue = read(snapshot, def.aliases);
        const sourceValues = [initial, adjusterValue, estimateValue, policyValue].filter(Boolean);
        const status: 'Match'|'Mismatch'|'Not Available' = sourceValues.length < 2 ? 'Not Available'
          : sourceValues.every(value => value.toLowerCase() === sourceValues[0].toLowerCase()) ? 'Match' : 'Mismatch';
        rows.push({key:def.key,label:def.label,initial:initial || 'Not Available',adjuster:adjusterValue || 'Not Available',estimate:estimateValue || 'Not Available',policy:policyValue || 'Not Available',status});
      }
    }
    return rows;
  }

  selectedComparisonSectionTitle(): string {
    const section = this.claimDetailSections().find(item => item.id === this.selectedComparisonSectionId);
    const subsection = section?.subsections.find(item => item.id === this.selectedComparisonSubsectionId);
    return subsection ? `${section?.title || 'Claim Review'} · ${subsection.title}` : section?.title || 'Initial Claim Review';
  }

  selectedComparisonRows(): ReturnType<AppComponent['claimReportComparisonRows']> {
    const section = this.claimDetailSections().find(item => item.id === this.selectedComparisonSectionId);
    return section ? this.comparisonRowsForSection(section, this.selectedComparisonSubsectionId) : [];
  }

  trackByClaimField(_index: number, field: {key: string}): string {
    return field.key;
  }
  claimDetailSections(): Array<{id: string; number: string; title: string; description: string; subsections: Array<{id: string; title: string; fieldKeys: string[]; configured?: boolean; note?: string}>}> {
    return [
      {id:'initial-review',number:'01',title:'Initial Claim Review',description:'Triage, status and first notice information',subsections:[
        {id:'claim-status',title:'Claim status and loss cause',fieldKeys:['status','loss_cause']}
      ]},
      {id:'claim-details',number:'02',title:'Claim Details',description:'Insured, incident and claim classification',subsections:[
        {id:'insured',title:'Insured and contact information',fieldKeys:['customer_name','email','phone','business_name','industry']},
        {id:'incident',title:'Incident and claim information',fieldKeys:['claim_type','incident_date','incident_description','commercial_liability_type']}
      ]},
      {id:'carrier-participation',number:'03',title:'Carrier Participation',description:'Participating carrier and share information',subsections:[
        {id:'carrier-1',title:'Carrier 1',fieldKeys:[],configured:false,note:'Carrier participation fields and share percentages are not currently present in the claim model.'},
        {id:'carrier-2',title:'Carrier 2',fieldKeys:[],configured:false},
        {id:'carrier-3',title:'Carrier 3',fieldKeys:[],configured:false},
        {id:'carrier-4',title:'Carrier 4',fieldKeys:[],configured:false},
        {id:'carrier-5',title:'Carrier 5',fieldKeys:[],configured:false},
        {id:'carrier-6',title:'Carrier 6',fieldKeys:[],configured:false}
      ]},
      {id:'policy-coverage-details',number:'04',title:'Policy and Coverage Details',description:'Policy data and coverage administration',subsections:[
        {id:'policy-information',title:'Policy Information',fieldKeys:['policy_number']},
        {id:'policy-period',title:'Policy Period',fieldKeys:[],configured:false},
        {id:'recovery-assessment',title:'Recovery Assessment',fieldKeys:[],configured:false},
        {id:'subrogation',title:'Subrogation',fieldKeys:[],configured:false},
        {id:'salvage',title:'Salvage',fieldKeys:[],configured:false},
        {id:'deductibles',title:'Deductibles',fieldKeys:[],configured:false},
        {id:'financial-information',title:'Financial Information',fieldKeys:['estimated_loss','currency']},
        {id:'mortgage-details',title:'Mortgage Details',fieldKeys:[],configured:false}
      ]},
      {id:'coverage-deductible',number:'05',title:'Coverage and Deductible',description:'Coverage review and applicable deductible details',subsections:[
        {id:'coverage-review',title:'Coverage and deductible review',fieldKeys:[],configured:false,note:'Coverage limits, insured interests and deductible values require verified policy and coverage records.'}
      ]},
      {id:'location-property',number:'06',title:'Location-Level Coverage and Property Characteristics',description:'Risk location, property and building details',subsections:[
        {id:'location-information',title:'Location Information',fieldKeys:['loss_location','country_code','region','locality','postal_code']},
        {id:'building-information',title:'Building Information',fieldKeys:[],configured:false},
        {id:'construction-occupancy',title:'Construction and Occupancy',fieldKeys:['property_occupancy']},
        {id:'roof-information',title:'Roof Information',fieldKeys:[],configured:false},
        {id:'property-coverage',title:'Property Coverage',fieldKeys:[],configured:false},
        {id:'endorsements',title:'Endorsements',fieldKeys:[],configured:false}
      ]},
      {id:'adjuster-report',number:'07',title:'Adjuster Report',description:'Inspection findings and adjuster documentation',subsections:[
        {id:'adjuster-report-content',title:'Adjuster report',fieldKeys:[],configured:false,note:'No adjuster report fields or linked report records are currently exposed in Claim Details.'}
      ]},
      {id:'estimate-reports',number:'08',title:'Estimate Reports',description:'Repair estimates and supporting reports',subsections:[
        {id:'estimate-report-content',title:'Estimate reports',fieldKeys:[],configured:false,note:'Estimate reports remain in the existing Financial Estimate workflow. This section will link verified report records when available.'}
      ]}
    ];
  }

  subsectionConnectionLabel(sectionId: string, subsection: {fieldKeys: string[]; configured?: boolean}): string {
    if (sectionId === 'adjuster-report') {
      return this.adjusterReportsBusy ? 'Loading adjuster reports…' : this.adjusterReports.length
        ? `Connected · ${this.adjusterReports.length} report(s) linked` : 'No adjuster report linked to this claim';
    }
    if (sectionId === 'estimate-reports') {
      return this.estimateBusy ? 'Loading estimate reports…' : this.financialEstimates.length
        ? `Connected · ${this.financialEstimates.length} estimate(s) linked` : 'No financial estimate saved for this claim';
    }
    if (sectionId === 'policy-coverage-details' && subsection.fieldKeys.length === 0) {
      const snapshot = this.coverageAssessments[0]?.policy_snapshot;
      if (snapshot?.effective_from || snapshot?.effective_to) return 'Connected · Policy period available in coverage record';
      if (this.coverageAssessments.length) return 'Coverage record found · Policy period not recorded';
      return 'No linked coverage assessment yet';
    }
    if (sectionId === 'coverage-deductible') {
      const snapshot = this.coverageAssessments[0]?.policy_snapshot;
      return snapshot ? 'Connected · Coverage assessment and policy snapshot available' : 'No linked coverage assessment yet';
    }
    if (subsection.fieldKeys.length) {
      const fields = this.claimDetailFields().filter(field => subsection.fieldKeys.includes(field.key));
      const populated = fields.filter(field => !!field.value.trim()).length;
      return populated ? `Connected · ${populated} of ${fields.length} fields populated` : 'Connected · No values recorded yet';
    }
    return 'Not yet connected to claim data';
  }

  claimFieldsFor(keys: string[]): Array<{key: string; label: string; value: string; type: string; required: boolean; options?: string[]}> {
    const fields = this.claimDetailFields().filter(field => keys.includes(field.key));
    const query = this.claimDetailSearch.trim().toLowerCase();
    return query ? fields.filter(field => field.label.toLowerCase().includes(query) || field.value.toLowerCase().includes(query) || field.key.toLowerCase().includes(query)) : fields;
  }

  sectionProgress(section: {subsections: Array<{fieldKeys: string[]}>}): {complete: number; total: number; pending: number} {
    const keys = [...new Set(section.subsections.flatMap(item => item.fieldKeys))];
    const fields = this.claimDetailFields().filter(field => keys.includes(field.key));
    const complete = fields.filter(field => !!field.value.trim()).length;
    return {complete,total:fields.length,pending:fields.length-complete};
  }

  claimOverallProgress(): {complete: number; total: number} {
    const sections = this.claimDetailSections();
    const keys = [...new Set(sections.flatMap(section => section.subsections.flatMap(item => item.fieldKeys)))];
    const fields = this.claimDetailFields().filter(field => keys.includes(field.key));
    return {complete:fields.filter(field => !!field.value.trim()).length,total:fields.length};
  }

  isClaimSectionExpanded(id: string): boolean { return this.expandedClaimSections.includes(id); }
  isClaimSubsectionExpanded(id: string): boolean { return this.expandedClaimSubsections.includes(id); }

  toggleClaimSection(id: string): void {
    this.selectedComparisonSectionId = id;
    this.selectedComparisonSubsectionId = '';
    this.expandedClaimSections = this.isClaimSectionExpanded(id) ? this.expandedClaimSections.filter(value => value !== id) : [...this.expandedClaimSections,id];
  }

  toggleClaimSubsection(id: string): void {
    const separator = id.indexOf(':');
    if (separator >= 0) {
      this.selectedComparisonSectionId = id.slice(0, separator);
      this.selectedComparisonSubsectionId = id.slice(separator + 1);
    }
    this.expandedClaimSubsections = this.isClaimSubsectionExpanded(id) ? this.expandedClaimSubsections.filter(value => value !== id) : [...this.expandedClaimSubsections,id];
  }

  expandAllClaimSections(): void {
    this.expandedClaimSections = this.claimDetailSections().map(section => section.id);
    this.expandedClaimSubsections = this.claimDetailSections().flatMap(section => section.subsections.map(item => section.id + ':' + item.id));
  }

  collapseAllClaimSections(): void {
    this.expandedClaimSections = [];
    this.expandedClaimSubsections = [];
  }

  onClaimDetailSearchChange(): void {
    const query = this.claimDetailSearch.trim().toLowerCase();
    if (!query) {
      this.expandedClaimSections = ['initial-review'];
      this.expandedClaimSubsections = ['initial-review:claim-status'];
      return;
    }
    const matchingSections: string[] = [];
    const matchingSubsections: string[] = [];
    for (const section of this.claimDetailSections()) {
      for (const subsection of section.subsections) {
        const matches = this.claimDetailFields().filter(field => subsection.fieldKeys.includes(field.key) &&
          (field.label.toLowerCase().includes(query) || field.value.toLowerCase().includes(query) || field.key.toLowerCase().includes(query)));
        if (matches.length) {
          matchingSections.push(section.id);
          matchingSubsections.push(section.id + ':' + subsection.id);
        }
      }
    }
    this.expandedClaimSections = [...new Set(matchingSections)];
    this.expandedClaimSubsections = [...new Set(matchingSubsections)];
  }

  trackByClaimSection(_index: number, section: {id: string}): string { return section.id; }
  trackByClaimSubsection(_index: number, subsection: {id: string}): string { return subsection.id; }



  startFieldEdit(field: {key: string; value: string}): void {
    if (!this.canEditClaims()) return;
    this.editingClaimField = field.key;
    this.claimFieldDraft = field.value;
    this.errorMessage = '';
    this.successMessage = '';
  }

  cancelFieldEdit(): void {
    this.editingClaimField = null;
    this.claimFieldDraft = '';
    this.claimFieldBusy = false;
  }

  saveClaimField(field: {key: string; label: string; required: boolean}): void {
    if (!this.selectedClaim || !this.canEditClaims() || this.claimFieldBusy) return;
    const raw = this.claimFieldDraft.trim();
    if (field.required && !raw) {
      this.errorMessage = `${field.label} is required and cannot be empty.`;
      return;
    }
    if (field.key === 'estimated_loss' && raw && (!Number.isFinite(Number(raw)) || Number(raw) < 0)) {
      this.errorMessage = 'Estimated loss must be a non-negative number.';
      return;
    }
    const value: any = raw === '' ? null : field.key === 'estimated_loss' ? Number(raw) : raw;
    this.persistClaimField(field.key, value, `${field.label} updated successfully.`);
  }

  clearClaimField(field: {key: string; label: string; required: boolean}): void {
    if (!this.selectedClaim || !this.canEditClaims() || field.required || this.claimFieldBusy) return;
    if (!window.confirm(`Clear only the "${field.label}" field? The rest of the claim will remain unchanged.`)) return;
    this.persistClaimField(field.key, null, `${field.label} cleared.`);
  }

  private persistClaimField(fieldName: string, value: any, message: string): void {
    if (!this.selectedClaim) return;
    const claimId = this.selectedClaim.claim_id;
    this.claimFieldBusy = true;
    this.errorMessage = '';
    this.http.patch<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}/field/${encodeURIComponent(fieldName)}`, {value}, {headers:this.authHeaders()}).subscribe({
      next: updated => {
        this.selectedClaim = updated;
        this.claims = this.claims.map(item => item.claim_id === updated.claim_id ? updated : item);
        this.claimFieldBusy = false;
        this.cancelFieldEdit();
        this.successMessage = message;
        this.loadLossCauseOptions();
        this.loadDashboard();
      },
      error: error => { this.claimFieldBusy = false; this.handleApiError(error); }
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

  exportClaim(claimId: string, format: string): void {
    if (!claimId || !['pdf', 'xlsx', 'csv', 'zip'].includes(format) || this.exportingClaimId) return;
    this.exportingClaimId = claimId;
    this.errorMessage = '';
    this.successMessage = '';
    this.http.get(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}/export`, {
      params: new HttpParams().set('format', format),
      headers: this.authHeaders(),
      responseType: 'blob'
    }).subscribe({
      next: blob => {
        this.exportingClaimId = '';
        const url = URL.createObjectURL(blob);
        const anchor = window.document.createElement('a');
        anchor.href = url;
        anchor.download = `${claimId}-export.${format === 'xlsx' ? 'xlsx' : format}`;
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        this.successMessage = `Export for ${claimId} downloaded as ${format.toUpperCase()}.`;
      },
      error: error => {
        this.exportingClaimId = '';
        this.errorMessage = error.error?.detail || `Unable to export ${claimId}. Please try again.`;
      }
    });
  }

  backToClaims(): void {
    this.selectedClaim = null;
    this.selectPage('Claims');
  }

  onFnolDocumentSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] || null;
    this.selectedFnolDocument = null;
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
    this.selectedFnolDocument = file;
  }

  submitFnol(): void {    this.errorMessage = '';
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
        const evidenceFile = this.selectedFnolDocument;
        this.draft = this.emptyDraft();
        this.selectedEmailIntakeId = '';
        this.selectedFnolDocument = null;
        this.loadClaims();
        this.loadDashboard();
        if (evidenceFile) {
          const body = new FormData();
          body.append('file', evidenceFile);
          this.http.post<ClaimDocument>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}/documents`, body, { headers: this.authHeaders() }).subscribe({
            next: uploaded => {
              this.savingClaim = false;
              this.successMessage = `Claim ${claim.claim_id} was created and ${uploaded.filename} was uploaded successfully.`;
            },
            error: error => {
              this.savingClaim = false;
              this.successMessage = `Claim ${claim.claim_id} was created successfully.`;
              this.errorMessage = `The claim was saved, but the evidence upload failed: ${error.error?.detail || 'Please open the claim and upload the document from Evidence & Documents.'}`;
            }
          });
        } else {
          this.savingClaim = false;
          this.successMessage = `Claim ${claim.claim_id} was created successfully.`;
        }
      },
      error: error => { this.savingClaim = false; this.handleApiError(error); }
    });
  }



  openAIProviderSetup(provider: string): void { this.selectedAIProvider = provider; this.errorMessage = ''; this.successMessage = ''; }

  closeAIProviderSetup(): void { this.selectedAIProvider = ''; }

  canManageEmailConnectors(): boolean {
    const roles = this.currentUser?.roles?.length ? this.currentUser.roles : [this.currentUser?.role || ''];
    return roles.includes('Super Administrator') || roles.includes('Insurance Company Administrator');
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
    const lossLocation = (f['loss_location'] || '').toLowerCase();
    const indianLocation = /\b(india|tamil nadu|coimbatore|chennai|bengaluru|bangalore|mumbai|pune|hyderabad|delhi|new delhi|kolkata|ahmedabad|jaipur|kochi|thiruvananthapuram|madurai|tiruchirappalli|salem|erode|tiruppur|vellore|mysuru|mysore)\b/.test(lossLocation);
    this.draft = {
      customer_name: f['customer_name'] || '',
      loss_cause: f['loss_cause'] || '',
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
      country_code: (f['country_code'] || (indianLocation ? 'IN' : '')).toUpperCase(),
      region: '',
      locality: '',
      postal_code: '',
      estimated_loss: f['estimated_loss'] && !isNaN(Number(f['estimated_loss'])) ? Number(f['estimated_loss']) : null,
      currency: (f['currency'] || (indianLocation ? 'INR' : '')).toUpperCase()
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
        this.loadDashboardAnalytics();
      },
      error: error => this.handleApiError(error)
    });
  }

  loadApplicableRepairRates(): void {
    const claimId = this.estimateClaimId.trim();
    if (!claimId) {
      this.errorMessage = 'Enter a Property Claim ID before loading repair catalogue rates.';
      return;
    }
    this.repairRatesLookupBusy = true;
    this.applicableRepairRates = [];
    this.selectedRepairRateId = '';
    this.http.get<any>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}/applicable-repair-rates`,
      { headers: this.authHeaders() }).subscribe({
        next: result => {
          this.applicableRepairRates = result.items || [];
          this.repairRatesLookupBusy = false;
          if (!this.applicableRepairRates.length) {
            this.successMessage = 'No active repair rates matched this Property claim location and loss date. Check the claim state/city and catalogue records.';
          } else {
            this.successMessage = `${this.applicableRepairRates.length} active repair rate(s) matched ${result.city}, ${result.state} for loss date ${result.pricing_date}.`;
          }
        },
        error: error => {
          this.repairRatesLookupBusy = false;
          this.handleApiError(error);
        }
      });
  }

  emptyEstimateLineItem(): any {
    return { description: '', quantity: 1, unit: 'item', unit_rate: 0, repair_rate_id: null, depreciation_rate: 0, taxable: true };
  }

  addEstimateLineItem(): void {
    if (this.estimateDraft.line_items.length >= 100) return;
    this.estimateDraft.line_items.push(this.emptyEstimateLineItem());
  }

  removeEstimateLineItem(index: number): void {
    if (this.estimateDraft.line_items.length <= 1) return;
    this.estimateDraft.line_items.splice(index, 1);
  }

  estimateLineTotal(item: any): number {
    const quantity = Number(item.quantity || 0);
    const rate = Number(item.unit_rate || 0);
    return Number.isFinite(quantity * rate) ? quantity * rate : 0;
  }

  selectRepairRateForLine(index: number, rateId: string): void {
    const line = this.estimateDraft.line_items[index];
    if (!line) return;
    if (!rateId) {
      line.repair_rate_id = null;
      return;
    }
    const rate = this.applicableRepairRates.find(item => item.rate_id === rateId);
    if (!rate) return;
    line.repair_rate_id = rate.rate_id;
    line.description = rate.item_name;
    line.unit = rate.unit;
    line.unit_rate = Number(rate.unit_rate);
    const sources = [...new Set(this.estimateDraft.line_items
      .filter((item: any) => item.repair_rate_id)
      .map((item: any) => {
        const selected = this.applicableRepairRates.find(rateItem => rateItem.rate_id === item.repair_rate_id);
        return selected ? `${selected.source_name} · ${selected.source_reference}` : '';
      }).filter(Boolean))];
    this.estimateDraft.pricing_source = sources.join(' | ') || '';
    this.estimateDraft.pricing_effective_date = rate.effective_from;
    this.successMessage = `Selected ${rate.item_name}. The server will revalidate this rate's status, location and effective date when calculating.`;
  }

  calculateFinancialEstimate(): void {
    const d = this.estimateDraft;
    const claim = this.claims.find(x => x.claim_id === this.estimateClaimId.trim());
    const currency = (claim?.currency || d.currency || '').toUpperCase();
    const countryCode = (claim?.country_code || d.country_code || '').toUpperCase();
    if (!this.estimateClaimId.trim() || !countryCode ||
        !d.state_or_jurisdiction.trim() || !currency || !/^[A-Z]{3}$/.test(currency) ||
        !/^[A-Z]{2}$/.test(countryCode) || !d.loss_date || !d.tax_point_date ||
        !d.pricing_source.trim() || !d.pricing_effective_date ||
        !Array.isArray(d.line_items) || d.line_items.length === 0 ||
        d.line_items.some((item: any) => !item.description?.trim() || !item.unit?.trim() ||
          Number(item.quantity) <= 0 || Number(item.unit_rate) < 0 ||
          Number(item.depreciation_rate) < 0 || Number(item.depreciation_rate) > 100)) {
      this.errorMessage = 'Enter a Claim ID, at least one complete repair line, valid quantities, ISO country/currency codes, loss/tax dates, jurisdiction and pricing evidence.';
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
      line_items: d.line_items.map((item: any) => ({
        description: item.description.trim(), quantity: Number(item.quantity), unit: item.unit.trim(),
        unit_rate: Number(item.unit_rate), repair_rate_id: item.repair_rate_id || null,
        taxable: !!item.taxable, depreciation_rate: Number(item.depreciation_rate || 0)
      })),
      source_damage_assessment_id: d.source_damage_assessment_id || null,
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

  loadAdjusterReports(): void {
    const id = this.selectedClaim?.claim_id;
    if (!id) { this.adjusterReports = []; return; }
    this.adjusterReportsBusy = true;
    this.http.get<{items: AdjusterReport[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/adjuster-reports`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.adjusterReports = result.items || []; this.adjusterReportsBusy = false; },
      error: error => { this.adjusterReports = []; this.adjusterReportsBusy = false; this.handleApiError(error); }
    });
  }

  comparisonIndicatorCount(status: 'Match' | 'Mismatch' | 'Not Available'): number {
    return this.selectedComparisonRows().filter(row => row.status === status).length;
  }

  comparisonMatchRate(): number {
    const matched = this.comparisonIndicatorCount('Match');
    const mismatched = this.comparisonIndicatorCount('Mismatch');
    const compared = matched + mismatched;
    return compared ? Math.round((matched / compared) * 100) : 0;
  }

  canAddComparisonComment(): boolean { return this.hasPermission('reports.comment'); }

  loadComparisonComments(): void {
    const id = this.selectedClaim?.claim_id;
    if (!id) { this.comparisonComments = []; return; }
    this.comparisonCommentsLoading = true;
    this.http.get<{items: ComparisonComment[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/comparison-comments`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.comparisonComments = result.items || []; this.comparisonCommentsLoading = false; },
      error: error => { this.comparisonComments = []; this.comparisonCommentsLoading = false; this.handleApiError(error); }
    });
  }

  addComparisonComment(): void {
    const claimId = this.selectedClaim?.claim_id;
    const comment = this.comparisonCommentDraft.trim();
    if (!claimId || comment.length < 2 || !this.canAddComparisonComment() || this.comparisonCommentBusy) return;
    const field = this.selectedComparisonRows().find(row => row.key === this.comparisonCommentFieldKey);
    const body = { comment, field_key: field?.key || null, field_label: field?.label || 'Overall comparison' };
    this.comparisonCommentBusy = true;
    this.http.post<ComparisonComment>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claimId)}/comparison-comments`, body, { headers: this.authHeaders() }).subscribe({
      next: saved => {
        this.comparisonComments = [saved, ...this.comparisonComments];
        this.comparisonCommentDraft = '';
        this.comparisonCommentBusy = false;
        this.successMessage = 'Comparison comment saved.';
      },
      error: error => { this.comparisonCommentBusy = false; this.handleApiError(error); }
    });
  }

  loadFinancialEstimates(): void {
    const id = this.selectedClaim?.claim_id || this.estimateClaimId.trim();
    if (!id) { this.financialEstimates = []; return; }
    this.estimateClaimId = id;
    this.http.get<{items: FinancialEstimate[]; count: number}>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(id)}/financial-estimates`, { headers: this.authHeaders() }).subscribe({
      next: result => this.financialEstimates = result.items,
      error: error => { this.financialEstimates = []; this.handleApiError(error); }
    });
  }

  canReviewFinancialEstimate(): boolean { return this.hasPermission('finance.edit') || this.hasPermission('finance.approve'); }

  canApproveFinancialEstimate(): boolean { return this.hasPermission('finance.approve'); }

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

  canReviewDamage(): boolean { return this.hasPermission('damage.review'); }

  canApproveDamage(): boolean { return this.hasPermission('claims.approve'); }

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

  prepareEstimateFromDamage(item: DamageAssessment): void {
    if (!item.claim_id) {
      this.errorMessage = 'This assessment is not linked to a claim. Link it to a Property claim before creating an estimate.';
      return;
    }
    if (!['Reviewed', 'Approved'].includes(item.status)) {
      this.errorMessage = 'An authorised adjuster must review the AI assessment before using it in a financial estimate.';
      return;
    }
    const claim = this.claims.find(row => row.claim_id === item.claim_id);
    this.estimateClaimId = item.claim_id;
    this.estimateDraft = {
      ...this.emptyEstimateDraft(),
      country_code: claim?.country_code || '',
      currency: claim?.currency || '',
      loss_date: claim?.incident_date || new Date().toISOString().slice(0, 10),
      description: [item.damage_category, ...(item.affected_areas || [])].filter(Boolean).join(' - ').slice(0, 200),
      source_damage_assessment_id: item.assessment_id,
      notes: `Prepared from reviewed AI damage assessment ${item.assessment_id}. AI observations are prompts for repair scoping only; verify all quantities, unit rates, depreciation and policy terms independently.`
    };
    this.financialEstimate = null;
    this.financialEstimates = [];
    this.applicableTaxRules = null;
    this.applicableRepairRates = [];
    this.selectedRepairRateId = '';
    this.activePage = 'Financial Estimate';
    this.errorMessage = '';
    this.successMessage = 'AI damage findings linked. Enter verified repair quantities, unit rates and policy calculation inputs before calculating.';
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
    this.loadCustomRoles();
    this.http.get<{items: User[]; count: number}>(`${environment.apiBaseUrl}/users`, { headers: this.authHeaders() }).subscribe({
      next: result => this.users = result.items,
      error: error => this.handleApiError(error)
    });
    this.http.get<{roles: string[]}>(`${environment.apiBaseUrl}/users/roles`, { headers: this.authHeaders() }).subscribe({
      next: result => this.roles = result.roles,
      error: error => this.handleApiError(error)
    });
  }

  loadCustomRoles(): void {
    if (!this.isAdmin()) return;
    this.http.get<{items: any[]; available_permissions: string[]}>(`${environment.apiBaseUrl}/roles/custom`, { headers: this.authHeaders() }).subscribe({
      next: result => { this.customRoles = result.items || []; this.availablePermissions = result.available_permissions || []; },
      error: error => this.handleApiError(error)
    });
  }

  toggleCustomRolePermission(permission: string, checked: boolean): void {
    const selected = new Set(this.customRoleDraft.permissions);
    if (checked) selected.add(permission); else selected.delete(permission);
    this.customRoleDraft.permissions = Array.from(selected);
  }

  editCustomRole(role: any): void {
    this.editingCustomRoleName = role.name;
    this.customRoleDraft = { name: role.name, description: role.description || '', permissions: [...(role.permissions || [])] };
  }

  cancelCustomRoleEdit(): void {
    this.editingCustomRoleName = '';
    this.customRoleDraft = { name: '', description: '', permissions: [] };
  }

  createCustomRole(): void {
    if (!this.customRoleDraft.name.trim() || this.customRoleDraft.permissions.length === 0) { this.errorMessage = 'Enter a role name and select at least one permission.'; return; }
    this.customRoleBusy = true; this.errorMessage = ''; this.successMessage = '';
    const request = this.editingCustomRoleName
      ? this.http.patch(`${environment.apiBaseUrl}/roles/custom/${encodeURIComponent(this.editingCustomRoleName)}`, { description: this.customRoleDraft.description, permissions: this.customRoleDraft.permissions }, { headers: this.authHeaders() })
      : this.http.post(`${environment.apiBaseUrl}/roles/custom`, this.customRoleDraft, { headers: this.authHeaders() });
    request.subscribe({
      next: () => { this.customRoleBusy = false; this.successMessage = this.editingCustomRoleName ? 'Custom role updated successfully.' : 'Custom role created successfully.'; this.cancelCustomRoleEdit(); this.loadCustomRoles(); },
      error: error => { this.customRoleBusy = false; this.handleApiError(error); }
    });
  }

  deleteCustomRole(role: any): void {
    if (!confirm('Delete custom role ' + role.name + '? It must not be assigned to users.')) return;
    this.http.delete(`${environment.apiBaseUrl}/roles/custom/${encodeURIComponent(role.name)}`, { headers: this.authHeaders() }).subscribe({
      next: () => { this.successMessage = 'Custom role deleted.'; this.loadCustomRoles(); },
      error: error => this.handleApiError(error)
    });
  }

  assignedRoleNames(account: User): string {
    return (account.roles?.length ? account.roles : [account.role]).filter(Boolean).join(', ');
  }

  customRoleNames(account: User): string {
    return (account.custom_roles || []).map((role: any) => typeof role === 'string' ? role : role.name).filter(Boolean).join(', ');
  }

  startUserRoleEdit(account: User): void {
    this.selectedUserForRoles = account;
    this.roleAssignmentDraft = {
      roles: [...(account.roles?.length ? account.roles : [account.role])],
      custom_roles: (account.custom_roles || []).map((role: any) => typeof role === 'string' ? role : role.name)
    };
  }

  toggleAssignedRole(role: string, checked: boolean): void {
    const selected = new Set(this.roleAssignmentDraft.roles);
    if (checked) selected.add(role); else selected.delete(role);
    this.roleAssignmentDraft.roles = Array.from(selected);
  }

  toggleAssignedCustomRole(role: string, checked: boolean): void {
    const selected = new Set(this.roleAssignmentDraft.custom_roles);
    if (checked) selected.add(role); else selected.delete(role);
    this.roleAssignmentDraft.custom_roles = Array.from(selected);
  }

  saveUserRoles(): void {
    if (!this.selectedUserForRoles || this.roleAssignmentDraft.roles.length === 0) {
      this.errorMessage = 'Every account must have at least one predefined role.';
      return;
    }
    this.roleAssignmentBusy = true;
    this.http.patch(`${environment.apiBaseUrl}/users/${encodeURIComponent(this.selectedUserForRoles.email)}/roles`, this.roleAssignmentDraft, { headers: this.authHeaders() }).subscribe({
      next: () => { this.roleAssignmentBusy = false; this.successMessage = 'User roles updated.'; this.selectedUserForRoles = null; this.loadUsers(); },
      error: error => { this.roleAssignmentBusy = false; this.handleApiError(error); }
    });
  }

  toggleNewUserRole(role: string, checked: boolean): void {
    const selected = new Set(this.newUser.roles);
    if (checked) selected.add(role); else selected.delete(role);
    this.newUser.roles = Array.from(selected);
  }

  createUser(): void {
    this.errorMessage = '';
    this.successMessage = '';
    this.userBusy = true;
    this.http.post<User>(`${environment.apiBaseUrl}/users`, this.newUser, { headers: this.authHeaders() }).subscribe({
      next: user => {
        this.userBusy = false;
        this.successMessage = `User ${user.email} created successfully.`;
        this.newUser = { full_name: '', email: '', password: '', roles: ['Claims Manager'] };
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

  formatAmount(amount: number | null | undefined, currency: string | null | undefined): string {
    if (amount === null || amount === undefined) return 'Not Found';
    if (!currency) return amount.toLocaleString();
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount); }
    catch { return currency + ' ' + amount.toLocaleString(); }
  }
}
