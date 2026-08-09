import AuthShell from '../../components/auth/AuthShell'

export const metadata = {
  title: 'Entrar · Don’tRipIt',
}

export default async function LoginPage({ searchParams }) {
  const params = await searchParams
  return <AuthShell mode="login" next={params?.next || '/dashboard'} />
}
