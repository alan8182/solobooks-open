import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { CompanyInfo, CreateCompanyRequest } from '@/api/types'
import { createCompany as apiCreateCompany, updateCompany as apiUpdateCompany } from '@/api/auth'
import { useAuthStore } from './auth'

export const useCompanyStore = defineStore('company', () => {
  const companies = ref<CompanyInfo[]>([])

  function setCompanies(list: CompanyInfo[]) {
    companies.value = list
  }

  async function create(data: CreateCompanyRequest): Promise<CompanyInfo> {
    const company = await apiCreateCompany(data)
    const authStore = useAuthStore()
    if (authStore.user) {
      authStore.user.companies.push(company)
    }
    return company
  }

  async function update(companyId: string, data: Partial<CreateCompanyRequest>) {
    const updated = await apiUpdateCompany(companyId, data)
    const idx = companies.value.findIndex((c) => c.company_id === companyId)
    if (idx >= 0) companies.value[idx] = updated
    return updated
  }

  return { companies, setCompanies, create, update }
})
