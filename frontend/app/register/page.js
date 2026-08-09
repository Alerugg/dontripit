import AuthShell from '../../components/auth/AuthShell'

export const metadata = {
  title: 'Crear cuenta · Don’tRipIt',
}

export default async function RegisterPage({ searchParams }) {
  const params = await searchParams
  return <AuthShell mode="register" next={params?.next || '/dashboard'} />
}
